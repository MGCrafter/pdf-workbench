"""Undoable visible-page crop and proportional resize of immutable PDF snapshots.

Crop only hides content. It is deliberately not a redaction or sanitization tool.
All dimensions are physical PDF points in the final workspace orientation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import io
import math
import re
from typing import Mapping, Sequence
from uuid import uuid4

from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, NumberObject, RectangleObject, TextStringObject

from .annotations import add_pdf_annotations, normalized_point_to_pdf, visible_page_box
from .bookmarks import remap_source_bookmarks
from .core import PageRef, PdfWorkbenchError, SourceDocument, _check_cancel
from .forms import copy_page_fields, page_has_fields
from .page_marks import target_pages

POSITIONS = ('center', 'top-left', 'top-center', 'top-right', 'center-left', 'center-right', 'bottom-left', 'bottom-center', 'bottom-right')


def _number(value, label, low=0, high=14400):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise PdfWorkbenchError(f'{label} muss zwischen {low:g} und {high:g} pt liegen.')
    return float(value)


@dataclass(frozen=True)
class PageGeometryOptions:
    crop_margins: tuple[float, float, float, float] | None = None
    target_size: tuple[float, float] | None = None
    margin: float = 0
    position: str = 'center'
    allow_upscale: bool = True

    def __post_init__(self):
        if (self.crop_margins is None) == (self.target_size is None):
            raise PdfWorkbenchError('Bitte Zuschneiden oder eine neue Seitengröße wählen.')
        try:
            if self.crop_margins is not None:
                if len(self.crop_margins) != 4: raise ValueError
                object.__setattr__(self, 'crop_margins', tuple(_number(v, 'Der Zuschnittrand') for v in self.crop_margins))
            if self.target_size is not None:
                if len(self.target_size) != 2: raise ValueError
                object.__setattr__(self, 'target_size', tuple(_number(v, 'Die Seitengröße', 18) for v in self.target_size))
        except (TypeError, ValueError):
            raise PdfWorkbenchError('Die Seitenabmessungen sind ungültig.') from None
        _number(self.margin, 'Der Innenrand')
        if self.position not in POSITIONS or type(self.allow_upscale) is not bool:
            raise PdfWorkbenchError('Die Einstellungen zur Seitenanpassung sind ungültig.')
        if self.target_size and min(self.target_size) <= self.margin * 2:
            raise PdfWorkbenchError('Der Innenrand lässt keine nutzbare Seitenfläche übrig.')


@dataclass(frozen=True)
class PageGeometryRequest:
    options: PageGeometryOptions
    target_ids: tuple[str, ...] | None = None

    def __post_init__(self):
        if not isinstance(self.options, PageGeometryOptions): raise PdfWorkbenchError('Die Seiteneinstellungen sind ungültig.')
        if self.target_ids is not None: object.__setattr__(self, 'target_ids', tuple(self.target_ids))


@dataclass(frozen=True)
class PageGeometryResult:
    sources: tuple[SourceDocument, ...]
    pages: tuple[PageRef, ...]
    changed_ids: tuple[str, ...]
    flattened_annotation_count: int = 0
    warnings: tuple[str, ...] = ()


def _point(matrix, x, y):
    a,b,c,d,e,f = matrix
    return a*x+c*y+e, b*x+d*y+f


def _inverse(matrix):
    a,b,c,d,e,f = matrix; det = a*d-b*c
    return (d/det,-b/det,-c/det,a/det,(c*f-d*e)/det,(b*e-a*f)/det)


def _compose(outer, inner):
    a,b,c,d,e,f = outer; g,h,i,j,k,l = inner
    return (a*g+c*h,b*g+d*h,a*i+c*j,b*i+d*j,a*k+c*l+e,b*k+d*l+f)


def _display(page, extra=0):
    l,b,r,t = visible_page_box(page)
    unit = float(page.get('/UserUnit', 1))
    if not math.isfinite(unit) or not 0 < unit <= 75000:
        raise PdfWorkbenchError('Die PDF-Seite besitzt eine ungültige Maßeinheit.')
    rotation = (page.rotation + extra) % 360
    if rotation not in (0,90,180,270): raise PdfWorkbenchError('Die Seitenrotation muss ein Vielfaches von 90 Grad sein.')
    matrices = {0:(unit,0,0,unit,-unit*l,-unit*b), 90:(0,-unit,unit,0,-unit*b,unit*r),
                180:(-unit,0,0,-unit,unit*r,unit*t), 270:(0,unit,-unit,0,unit*t,-unit*l)}
    size = ((t-b)*unit,(r-l)*unit) if rotation % 180 else ((r-l)*unit,(t-b)*unit)
    return matrices[rotation], size


def page_display_size(source, page):
    """Physical visible size for labels and keyboard crop controls."""
    reader = PdfReader(io.BytesIO(source.data))
    return _display(reader.pages[page.page_index], page.rotation)[1]


def _bounds(points):
    points=tuple(points)
    return min(x for x,y in points), min(y for x,y in points), max(x for x,y in points), max(y for x,y in points)


def _rect_points(rect):
    l,b,r,t = rect
    return ((l,b),(r,b),(l,t),(r,t))


def _normalized(page, x, y):
    matrix, size = _display(page)
    px,py = _point(matrix,x,y)
    return px/size[0], 1-py/size[1]


def _model(annotation, old_page, new_page, matrix, scale, cropping):
    def point(x,y): return _normalized(new_page, *_point(matrix,*normalized_point_to_pdf(old_page,x,y)))
    def rect(value):
        l,t,r,b = value
        return _bounds((point(l,t),point(r,t),point(l,b),point(r,b)))
    bounds = rect(annotation.rect)
    rects = tuple(rect(r) for r in annotation.rects)
    if cropping and any(v < -1e-8 or v > 1+1e-8 for box in (bounds,*rects) for v in box): return None
    def clamp(box): return tuple(max(0.,min(1.,v)) for v in box)
    style = annotation.style
    if annotation.kind == 'text' and not math.isclose(scale,1):
        if not 4 <= style.font_size*scale <= 288:
            raise PdfWorkbenchError('Die Skalierung würde ein eigenes Textobjekt kleiner als 4 oder größer als 288 pt machen. Bitte eine andere Zielgröße wählen.')
        style = replace(style,font_size=style.font_size*scale)
    # Signature stroke points are local fractions of their rect, not page points.
    return replace(annotation,rect=clamp(bounds),rects=tuple(clamp(r) for r in rects),style=style)


def _array(values): return ArrayObject(FloatObject(v) for v in values)


def _scale_da(value, scale):
    return TextStringObject(re.sub(r'(?P<size>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s+Tf\b',
        lambda match: f'{float(match["size"])*scale:.9f} Tf',str(value)))


def _native_annotations(page, matrix):
    scale = math.hypot(matrix[0],matrix[1])
    def coordinates(value):
        values = list(value)
        if len(values)%2: raise PdfWorkbenchError('Eine PDF-Anmerkung enthält ungültige Koordinaten.')
        return _array(v for i in range(0,len(values),2) for v in _point(matrix,float(values[i]),float(values[i+1])))
    for ref in page.get('/Annots',[]):
        annotation = ref.get_object()
        if '/Rect' in annotation:
            annotation[NameObject('/Rect')] = _array(_bounds(_point(matrix,*p) for p in _rect_points(annotation['/Rect'])))
        for key in ('/QuadPoints','/L','/Vertices','/CL'):
            if key in annotation: annotation[NameObject(key)] = coordinates(annotation[key])
        if '/InkList' in annotation: annotation[NameObject('/InkList')] = ArrayObject(coordinates(stroke) for stroke in annotation['/InkList'])
        if '/RD' in annotation: annotation[NameObject('/RD')] = _array(float(v)*scale for v in annotation['/RD'])
        if '/Border' in annotation:
            border = annotation['/Border']; values = [float(v)*scale for v in border[:3]]
            annotation[NameObject('/Border')] = ArrayObject([*(FloatObject(v) for v in values), *([_array(float(v)*scale for v in border[3])] if len(border)>3 else [])])
        bs = annotation.get('/BS')
        if bs is not None:
            # Style dictionaries can be shared by several native annotations.
            # Detach before scaling or the same width would be multiplied twice.
            bs=DictionaryObject(bs.get_object())
            annotation[NameObject('/BS')]=bs
            if '/W' in bs: bs[NameObject('/W')]=FloatObject(float(bs['/W'])*scale)
            if '/D' in bs: bs[NameObject('/D')]=_array(float(v)*scale for v in bs['/D'])
        da = annotation.get('/DA')
        parent = annotation.get('/Parent')
        seen=set()
        while da is None and parent is not None:
            parent=parent.get_object()
            if id(parent) in seen: break
            seen.add(id(parent)); da=parent.get('/DA'); parent=parent.get('/Parent')
        if da is not None: annotation[NameObject('/DA')] = _scale_da(da,scale)


def _change(added, source_page, extra, options):
    before, (width,height) = _display(source_page,extra)
    if options.crop_margins is not None:
        l,t,r,b = options.crop_margins
        if width-l-r < 1 or height-t-b < 1:
            raise PdfWorkbenchError('Der Zuschnitt lässt auf mindestens einer Seite keine nutzbare Fläche übrig.')
        inverse = _inverse(before)
        added.cropbox=RectangleObject(_bounds(_point(inverse,*p) for p in _rect_points((l,b,width-r,height-t))))
        return (1,0,0,1,0,0), 1.
    # Native APs have their own clipping space. Expanding a previously clipped
    # widget could expose content again, especially after a later refill.
    # Reject that case atomically instead of silently changing its appearance.
    visible=visible_page_box(source_page)
    for ref in added.get('/Annots',[]):
        annotation=ref.get_object()
        if int(annotation.get('/F',0)) & (1|2):continue
        rect=annotation.get('/Rect')
        if rect is not None:
            values=tuple(map(float,rect))
            if (len(values)!=4 or any(not math.isfinite(v) for v in values) or
                    values[0]<visible[0]-1e-8 or values[1]<visible[1]-1e-8 or
                    values[2]>visible[2]+1e-8 or values[3]>visible[3]+1e-8):
                raise PdfWorkbenchError('Diese Seite enthält bereits am bisherigen Seitenrand abgeschnittene PDF-Anmerkungen oder Formularfelder. Eine neue Papiergröße könnte verborgene Darstellung zeigen; die Anpassung wurde vollständig abgebrochen.')
    tw,th=options.target_size
    scale=min((tw-2*options.margin)/width,(th-2*options.margin)/height)
    if not options.allow_upscale: scale=min(1.,scale)
    vertical,horizontal=options.position.split('-') if options.position!='center' else ('center','center')
    dx=options.margin if horizontal=='left' else (tw-options.margin-width*scale if horizontal=='right' else (tw-width*scale)/2)
    dy=options.margin if vertical=='bottom' else (th-options.margin-height*scale if vertical=='top' else (th-height*scale)/2)
    rotation=(source_page.rotation+extra)%360
    raw_size=(th,tw) if rotation%180 else (tw,th)
    added[NameObject('/UserUnit')]=NumberObject(1)
    added.mediabox=RectangleObject((0,0,*raw_size)); added.cropbox=RectangleObject((0,0,*raw_size))
    for key in ('/TrimBox','/BleedBox','/ArtBox'):
        if key in added: added[NameObject(key)]=RectangleObject((0,0,*raw_size))
    after,_=_display(added,extra)
    matrix=_compose(_inverse(after),_compose((scale,0,0,scale,dx,dy),before))
    # Preserve the old visible window when fitting onto a larger paper canvas.
    # The hidden original operators remain in the PDF; this is only a clip.
    content=added.get_contents()
    if content is not None:
        l,b,r,t=visible_page_box(source_page)
        stream=DecodedStreamObject()
        stream.set_data((f'q\n{l:.9f} {b:.9f} {r-l:.9f} {t-b:.9f} re W n\n'.encode('ascii')
                         +content.get_data()+b'\nQ\n'))
        added.replace_contents(stream)
    added.add_transformation(Transformation(matrix))
    _native_annotations(added,matrix)
    return matrix,scale


def prepare_page_geometry(sources: Mapping[str,SourceDocument], pages: Sequence[PageRef], options: PageGeometryOptions,
                          target_ids=None,cancel=None,progress=None,*,preview_uid=None)->PageGeometryResult:
    """Prepare all affected source copies together; caller commits one undo step."""
    request=PageGeometryRequest(options,target_ids); pages=tuple(pages)
    targets=target_pages(pages,request.target_ids); ids={p.uid for _,p in targets}
    if preview_uid is not None and preview_uid not in ids: raise PdfWorkbenchError('Die Vorschauseite gehört nicht zur Seitenauswahl.')
    affected={p.source_id for _,p in targets}
    workset=[p for p in pages if p.source_id in affected and (preview_uid is None or p.uid==preview_uid)]
    states={}; replacements={}; changed=[]; demoted=0
    for index,page in enumerate(workset):
        _check_cancel(cancel)
        if page.source_id not in sources: raise PdfWorkbenchError('Die ursprüngliche PDF-Quelle wurde nicht gefunden.')
        source=sources[page.source_id]
        if source.source_id not in states:
            reader,writer=PdfReader(io.BytesIO(source.data)),PdfWriter()
            writer.pdf_header='%PDF-1.7'
            if reader.metadata: writer.add_metadata({k:v for k,v in reader.metadata.items() if isinstance(v,str)})
            states[source.source_id]=(source,reader,writer,uuid4().hex,[])
        _,reader,writer,new_id,old_indexes=states[source.source_id]
        if not 0<=page.page_index<len(reader.pages) or page.rotation%90: raise PdfWorkbenchError('Die Arbeitsseite ist ungültig.')
        original=reader.pages[page.page_index]
        writer.reset_translation(reader)
        added=writer.add_page(original,excluded_keys=('/Annots',)) if page_has_fields(original) else writer.add_page(original)
        copy_page_fields(writer,added,reader,original,namespace=source.source_id)
        old_indexes.append(page.page_index)
        annotations=page.annotations
        if page.uid in ids:
            matrix,scale=_change(added,original,page.rotation,options)
            transformed=[]
            for annotation in annotations:
                _check_cancel(cancel)
                model=_model(annotation,original,added,matrix,scale,options.crop_margins is not None)
                if model is not None: transformed.append(model)
                else:
                    # Retain the original AP, including portions outside CropBox;
                    # do not claim its clipped geometry is an editable model.
                    add_pdf_annotations(writer,added,original,(annotation,))
                    added['/Annots'][-1].get_object().pop('/PDFWorkbench',None)
                    demoted+=1
            annotations=tuple(transformed); changed.append(page.uid)
        replacement=replace(page,source_id=new_id,page_index=len(writer.pages)-1,annotations=annotations)
        if preview_uid is not None:
            add_pdf_annotations(writer,added,added,annotations)
            replacement=replace(replacement,annotations=())
        replacements[page.uid]=replacement
        if progress: progress(round((index+1)*100/len(workset)),'Seitengeometrie wird vorbereitet …')
    result_sources=[]
    for source,_,writer,new_id,old_indexes in states.values():
        _check_cancel(cancel); stream=io.BytesIO(); writer.write(stream)
        sizes=tuple((float(p.mediabox.width),float(p.mediabox.height)) for p in writer.pages)
        result_sources.append(replace(source,source_id=new_id,data=stream.getvalue(),page_count=len(sizes),page_sizes=sizes,page_annotations=(),
                                      bookmarks=remap_source_bookmarks(source.bookmarks,old_indexes)))
    _check_cancel(cancel)
    warnings=(f'{demoted} angeschnittene oder verborgene eigene Objekte bleiben als normale PDF-Anmerkungen erhalten und sind hier nicht mehr direkt bearbeitbar.',) if demoted else ()
    return PageGeometryResult(tuple(result_sources),tuple(replacements.get(p.uid,p) for p in pages),tuple(changed),demoted,warnings)


def apply_page_geometry(workspace,request,cancel=None,progress=None):
    result=prepare_page_geometry(workspace.sources,workspace.pages,request.options,request.target_ids,cancel,progress)
    _check_cancel(cancel); workspace.apply_page_replacements(result.sources,result.pages)
    return len(result.changed_ids)
