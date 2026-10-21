"""Immutable page bookmarks; imported actions are reduced to safe page targets.

Only titles, hierarchy and internal page destinations cross the import/export
boundary. No PDF action object is ever copied. Export uses page-fit destinations
so reordered, rotated or cropped pages do not inherit stale view coordinates.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import uuid4

from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject, TextStringObject

MAX_BOOKMARKS=5000
MAX_BOOKMARK_DEPTH=32


def _error(message):
    from .core import PdfWorkbenchError
    return PdfWorkbenchError(message)


def _title(value):
    if (not isinstance(value,str) or not value.strip() or len(value)>500
            or any(ord(char)<32 for char in value)):
        raise _error("Ein Lesezeichentitel benötigt 1 bis 500 Zeichen ohne Steuerzeichen.")
    return value.strip()


@dataclass(frozen=True)
class SourceBookmark:
    title: str
    page_index: int | None = None
    children: tuple[SourceBookmark,...] = ()

    def __post_init__(self):
        object.__setattr__(self,"title",_title(self.title));object.__setattr__(self,"children",tuple(self.children))
        if self.page_index is not None and (type(self.page_index) is not int or self.page_index<0):
            raise _error("Das Lesezeichen verweist auf eine ungültige Quellseite.")
        if any(not isinstance(item,SourceBookmark) for item in self.children):raise _error("Die Lesezeichenhierarchie ist ungültig.")


@dataclass(frozen=True)
class WorkspaceBookmark:
    uid: str
    title: str
    page_uid: str | None = None
    children: tuple[WorkspaceBookmark,...] = ()

    def __post_init__(self):
        if not isinstance(self.uid,str) or not self.uid or len(self.uid)>128:raise _error("Die Lesezeichenkennung ist ungültig.")
        object.__setattr__(self,"title",_title(self.title));object.__setattr__(self,"children",tuple(self.children))
        if self.page_uid is not None and (not isinstance(self.page_uid,str) or not self.page_uid):raise _error("Das Lesezeichenziel ist ungültig.")
        if any(not isinstance(item,WorkspaceBookmark) for item in self.children):raise _error("Die Lesezeichenhierarchie ist ungültig.")


def validate_bookmarks(bookmarks,page_uids=None):
    """Validate a bounded tree, optionally including all live page references."""
    seen=set();stack=[(item,1) for item in reversed(tuple(bookmarks))]
    while stack:
        item,depth=stack.pop()
        if (not isinstance(item,WorkspaceBookmark) or item.uid in seen
                or len(seen)>=MAX_BOOKMARKS or depth>MAX_BOOKMARK_DEPTH):
            raise _error("Lesezeichen müssen eindeutige Kennungen haben; höchstens 5.000 Einträge und 32 Ebenen sind möglich.")
        seen.add(item.uid)
        if page_uids is not None and item.page_uid is not None and item.page_uid not in page_uids:
            raise _error("Ein Lesezeichen verweist auf eine fehlende Arbeitsseite.")
        stack.extend((child,depth+1) for child in reversed(item.children))


def validate_source_bookmarks(bookmarks,page_count):
    seen=set();stack=[(item,1) for item in reversed(tuple(bookmarks))]
    while stack:
        item,depth=stack.pop()
        if (not isinstance(item,SourceBookmark) or id(item) in seen or len(seen)>=MAX_BOOKMARKS
                or depth>MAX_BOOKMARK_DEPTH or item.page_index is not None and item.page_index>=page_count):
            raise _error("Die Quelllesezeichen sind ungültig oder zu umfangreich.")
        seen.add(id(item));stack.extend((child,depth+1) for child in reversed(item.children))


def bookmark_location(bookmarks,uid,parent_uid=None):
    for index,item in enumerate(bookmarks):
        if item.uid==uid:return parent_uid,index,item
        found=bookmark_location(item.children,uid,item.uid)
        if found is not None:return found
    return None


def replace_bookmark(bookmarks,uid,replacement):
    if bookmark_location(bookmarks,uid) is None:raise _error("Das Lesezeichen wurde nicht gefunden.")
    return tuple(replacement if item.uid==uid else replace(item,children=replace_bookmark(item.children,uid,replacement))
                 if bookmark_location(item.children,uid) else item for item in bookmarks)


def delete_bookmark(bookmarks,uid,*,keep_children=False):
    if bookmark_location(bookmarks,uid) is None:raise _error("Das Lesezeichen wurde nicht gefunden.")
    def remove(items):
        result=[]
        for item in items:
            if item.uid==uid:
                if keep_children:result.extend(item.children)
            else:result.append(replace(item,children=remove(item.children)))
        return tuple(result)
    return remove(bookmarks)


def insert_bookmark(bookmarks,item,*,parent_uid=None,index=None):
    if parent_uid is None:
        result=list(bookmarks);position=len(result) if index is None else index
        if type(position) is not int or not 0<=position<=len(result):raise _error("Die Lesezeichenposition ist ungültig.")
        result.insert(position,item);return tuple(result)
    found=bookmark_location(bookmarks,parent_uid)
    if found is None:raise _error("Das übergeordnete Kapitel wurde nicht gefunden.")
    parent=found[2];children=insert_bookmark(parent.children,item,index=index)
    return replace_bookmark(bookmarks,parent_uid,replace(parent,children=children))


def move_bookmark(bookmarks,uid,*,parent_uid=None,index=None):
    found=bookmark_location(bookmarks,uid)
    if found is None:raise _error("Das Lesezeichen wurde nicht gefunden.")
    item=found[2]
    if parent_uid==uid or parent_uid is not None and bookmark_location(item.children,parent_uid):
        raise _error("Ein Kapitel kann nicht in sich selbst verschoben werden.")
    return insert_bookmark(delete_bookmark(bookmarks,uid),item,parent_uid=parent_uid,index=index)


def prune_bookmarks(bookmarks,page_uids):
    """Remove absent targets; a surviving subtree retains its chapter heading."""
    result=[]
    for item in bookmarks:
        children=prune_bookmarks(item.children,page_uids)
        if item.page_uid is not None and item.page_uid not in page_uids:
            if not children:continue
            result.append(replace(item,page_uid=None,children=children))
        elif item.page_uid is None and item.children and not children:
            continue
        else:result.append(replace(item,children=children))
    return tuple(result)


def source_to_workspace(bookmarks,pages):
    targets={}
    for page in pages:targets.setdefault(page.page_index,page.uid)
    def convert(items):
        result=[]
        for item in items:
            children=convert(item.children)
            target=targets.get(item.page_index)
            if item.page_index is not None and target is None and not children:continue
            result.append(WorkspaceBookmark(uuid4().hex,item.title,target,children))
        return tuple(result)
    return convert(bookmarks)


def remap_source_bookmarks(bookmarks,page_indexes):
    """Map old source pages to their first rebuilt copy, pruning missing pages."""
    targets={}
    for new_index,old_index in enumerate(page_indexes):targets.setdefault(old_index,new_index)
    def convert(items):
        result=[]
        for item in items:
            children=convert(item.children);target=targets.get(item.page_index)
            if item.page_index is not None and target is None and not children:continue
            if item.page_index is None and item.children and not children:continue
            result.append(replace(item,page_index=target,children=children))
        return tuple(result)
    return convert(bookmarks)


def bookmarks_for_sources(sources,pages):
    result=[];seen=set()
    for page in pages:
        if page.source_id in seen:continue
        seen.add(page.source_id);source=sources.get(page.source_id)
        if source:result.extend(source_to_workspace(source.bookmarks,[p for p in pages if p.source_id==page.source_id]))
    return tuple(result)


def write_bookmarks(writer,pages,bookmarks,cancel=None):
    validate_bookmarks(bookmarks)
    targets={}
    for index,page in enumerate(pages):targets.setdefault(page.uid,index)
    def write(items,parent=None):
        for item in items:
            if cancel and cancel():
                from .core import OperationCancelled
                raise OperationCancelled("Vorgang abgebrochen. Es wurde keine Datei gespeichert.")
            reference=writer.add_outline_item(item.title,targets.get(item.page_uid),parent=parent)
            write(item.children,reference)
    write(prune_bookmarks(bookmarks,set(targets)))


def read_source_bookmarks(reader,cancel=None):
    """Read only safe internal destinations; malformed/active entries are omitted.

    Named destinations are resolved from the PDF's destination trees, never via
    arbitrary action chains. Traversal budgets cover malformed cyclic PDFs.
    Returns immutable bookmarks and short aggregate warnings without URLs/data.
    """
    root=reader.trailer["/Root"];outline=root.get("/Outlines")
    if outline is None:return (),()
    counts={"active":0,"invalid":0,"views":0,"limit":0}
    def obj(value):return value.get_object() if hasattr(value,"get_object") else value
    def check_cancel():
        if cancel and cancel():
            from .core import OperationCancelled
            raise OperationCancelled("Import abgebrochen.")
    targets={};direct={}
    for index,page in enumerate(reader.pages):
        ref=page.indirect_reference
        if ref is not None:targets[(ref.idnum,ref.generation)]=index
        direct[id(page)]=index
    names={};name_seen=set()
    def collect_names(node,depth=0):
        check_cancel();node=obj(node)
        if not isinstance(node,DictionaryObject) or id(node) in name_seen or depth>MAX_BOOKMARK_DEPTH or len(name_seen)>MAX_BOOKMARKS:
            counts["invalid"]+=1;return
        name_seen.add(id(node));pairs=obj(node.get("/Names",[]))
        if isinstance(pairs,(list,ArrayObject)) and len(pairs)%2==0:
            for index in range(0,min(len(pairs),MAX_BOOKMARKS*2),2):names[str(pairs[index])]=pairs[index+1]
        for kid in obj(node.get("/Kids",[])):
            if len(name_seen)>=MAX_BOOKMARKS:counts["limit"]+=1;break
            collect_names(kid,depth+1)
    try:
        old=obj(root.get("/Dests",{}))
        if isinstance(old,DictionaryObject):
            for index,(key,value) in enumerate(old.items()):
                if index>=MAX_BOOKMARKS:counts["limit"]+=1;break
                names[str(key)]=value
        catalog_names=obj(root.get("/Names",{}))
        if isinstance(catalog_names,DictionaryObject) and "/Dests" in catalog_names:collect_names(catalog_names["/Dests"])
    except (AttributeError,TypeError,KeyError):counts["invalid"]+=1
    def destination(value,seen=(),depth=0):
        if depth>MAX_BOOKMARK_DEPTH:return None
        value=obj(value)
        if isinstance(value,(NameObject,TextStringObject,str)):
            key=str(value)
            if key in seen or len(seen)>MAX_BOOKMARK_DEPTH:return None
            resolved=names.get(key,names.get(key.removeprefix("/")))
            return destination(resolved,(*seen,key),depth+1) if resolved is not None else None
        if isinstance(value,DictionaryObject):return destination(value.get("/D"),seen,depth+1)
        if not isinstance(value,(ArrayObject,list)) or not value:return None
        page=value[0];ref=page if isinstance(page,IndirectObject) else getattr(page,"indirect_reference",None)
        target=targets.get((ref.idnum,ref.generation)) if ref is not None else direct.get(id(page))
        if target is not None and (len(value)<2 or str(value[1])!="/Fit"):counts["views"]+=1
        return target
    seen=set()
    def linked(first,depth):
        result=[];current=first
        while current is not None:
            check_cancel()
            if len(seen)>=MAX_BOOKMARKS or depth>MAX_BOOKMARK_DEPTH:counts["limit"]+=1;break
            try:node=obj(current)
            except Exception:counts["invalid"]+=1;break
            if not isinstance(node,DictionaryObject) or id(node) in seen:counts["invalid"]+=1;break
            seen.add(id(node));current=node.get("/Next")
            children=linked(node["/First"],depth+1) if "/First" in node else ()
            title=" ".join(str(node.get("/Title","")).split())
            if not title or len(title)>500:counts["invalid"]+=1;result.extend(children);continue
            target=None;active=False;has_target=False
            try:
                if "/A" in node:
                    action=obj(node["/A"]);has_target=True
                    if not isinstance(action,DictionaryObject) or action.get("/S")!="/GoTo" or "/Next" in action:
                        counts["active"]+=1;active=True
                    else:target=destination(action.get("/D"))
                elif "/Dest" in node:has_target=True;target=destination(node["/Dest"])
            except Exception:counts["invalid"]+=1;has_target=True
            if has_target and target is None:
                if not active:counts["invalid"]+=1
                if not children:continue
            result.append(SourceBookmark(title,target,children))
        return tuple(result)
    try:bookmarks=linked(obj(outline).get("/First"),1)
    except (AttributeError,TypeError,KeyError):counts["invalid"]+=1;bookmarks=()
    warnings=[]
    if counts["active"]:warnings.append(f"{counts['active']} Lesezeichen mit externen oder aktiven Aktionen wurden ausgelassen; sichere Unterkapitel bleiben erhalten.")
    if counts["invalid"]:warnings.append(f"{counts['invalid']} ungültige oder nicht auflösbare Lesezeicheneinträge wurden ausgelassen.")
    if counts["views"]:warnings.append("Importierte Lesezeichen behalten ihre Seitenziele; Zoom- und Positionsvorgaben werden auf „Seite einpassen“ vereinfacht.")
    if counts["limit"]:warnings.append("Die Lesezeichen sind zu umfangreich oder zu tief; übernommen wurden höchstens 5.000 Einträge in 32 Ebenen.")
    return bookmarks,tuple(warnings)
