"""Render stored Widget appearances with QtPdf without changing real PDFs.

QtPdf does not paint AcroForm Widgets with its Annotations render flag. This
transient copy places their normal appearance streams into the page contents.
The original source bytes and the interactive export path remain untouched.
"""
from __future__ import annotations

import io
import math

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

from .forms import _object, _widgets, validate_forms


def _cancel(check):
    if check and check():
        from .core import OperationCancelled
        raise OperationCancelled("Formularvorschau abgebrochen. Keine Dateien verändert.")


def _error():
    from .core import PdfWorkbenchError
    return PdfWorkbenchError("Eine Formulardarstellung konnte nicht sicher für die Vorschau übernommen werden.")


def _appearance(widget):
    normal=_object(_object(widget.get("/AP",{})).get("/N"))
    if normal is None:raise _error()
    if not hasattr(normal,"get_data"):
        state=widget.get("/AS")
        if state is None or state not in normal:raise _error()
        normal=_object(normal[state])
    if not hasattr(normal,"get_data"):raise _error()
    return normal


def _placement(widget,appearance):
    """Map the appearance's Matrix-transformed BBox onto annotation Rect.

    Form /Matrix is applied by PDF's Do operator itself. The outer matrix maps
    its resulting bounding rectangle onto the Widget rectangle, as PDF viewers
    do for appearance placement (including nonzero BBox origins).
    """
    try:
        box=tuple(map(float,appearance["/BBox"]));matrix=tuple(map(float,appearance.get("/Matrix",(1,0,0,1,0,0))))
        rect=tuple(map(float,widget["/Rect"]))
        if (len(box)!=4 or len(rect)!=4 or len(matrix)!=6
                or any(not math.isfinite(number) for number in (*box,*rect,*matrix))):raise ValueError
        a,b,c,d,e,f=matrix
        corners=[(a*x+c*y+e,b*x+d*y+f) for x,y in ((box[0],box[1]),(box[0],box[3]),(box[2],box[1]),(box[2],box[3]))]
        left,bottom=min(x for x,y in corners),min(y for x,y in corners)
        right,top=max(x for x,y in corners),max(y for x,y in corners)
        if (right<=left or top<=bottom or rect[2]<=rect[0] or rect[3]<=rect[1]
                or abs(a*d-b*c)<1e-12):raise ValueError
        sx,sy=(rect[2]-rect[0])/(right-left),(rect[3]-rect[1])/(top-bottom)
        result=(sx,0,0,sy,rect[0]-sx*left,rect[1]-sy*bottom)
        if any(not math.isfinite(number) for number in result):raise ValueError
        return result
    except (KeyError,ValueError,TypeError,OverflowError):raise _error() from None


def form_render_data(source,cancel=None)->bytes:
    """Return render-only bytes with visible Widget normal APs flattened.

    Invisible, Hidden and NoView annotation flags suppress the Widget in a
    screen preview. Form /AS selects a button state; no state is guessed. The
    returned document has no interactive fields and must never be exported in
    place of ``source.data``. Every public edit/export API retains real fields.
    """
    _cancel(cancel)
    reader=PdfReader(io.BytesIO(source.data))
    if not any(any(_widgets(page)) for page in reader.pages):return source.data
    validate_forms(reader);_cancel(cancel)
    writer=PdfWriter(clone_from=reader)
    for page in writer.pages:
        _cancel(cancel);widgets=list(_widgets(page))
        if not widgets:continue
        resources=DictionaryObject(_object(page.get("/Resources",{})))
        xobjects=DictionaryObject(_object(resources.get("/XObject",{})))
        resources[NameObject("/XObject")]=xobjects;page[NameObject("/Resources")]=resources
        commands=[]
        for index,widget in enumerate(widgets):
            _cancel(cancel)
            if int(widget.get("/F",0)) & (1|2|32):continue
            appearance=_appearance(widget);matrix=_placement(widget,appearance)
            name=f"/WBWidget{index}"
            while name in xobjects:name+="_"
            xobjects[NameObject(name)]=writer._add_object(appearance)
            transform=" ".join(f"{number:.12g}" for number in matrix)
            commands.append(f"q {transform} cm {name} Do Q\n")
        if commands:
            # Original page content can legally leave a CTM or clip active.
            # Widgets are painted independently of that content graphics state.
            prefix=DecodedStreamObject();prefix.set_data(b"q\n")
            stream=DecodedStreamObject();stream.set_data(("\nQ\n"+"".join(commands)).encode("ascii"))
            old=_object(page.get("/Contents",ArrayObject()))
            contents=ArrayObject([writer._add_object(prefix),*(old if isinstance(old,ArrayObject) else [page.raw_get("/Contents")])])
            contents.append(writer._add_object(stream));page[NameObject("/Contents")]=contents
        page[NameObject("/Annots")]=ArrayObject(reference for reference in page.get("/Annots",[])
            if _object(reference).get("/Subtype")!="/Widget")
    writer._root_object.pop("/AcroForm",None)
    _cancel(cancel);output=io.BytesIO();writer.write(output);_cancel(cancel)
    return output.getvalue()
