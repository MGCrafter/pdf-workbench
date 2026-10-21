"""Keyboard-accessible outline editor; commits one immutable tree on accept."""
from dataclasses import replace

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QComboBox,QDialog,QDialogButtonBox,QFormLayout,QHBoxLayout,
    QLabel,QLineEdit,QPushButton,QSplitter,QTreeWidget,QTreeWidgetItem,QVBoxLayout,QWidget)

from ..bookmarks import bookmark_location,validate_bookmarks
from ..core import PdfWorkbenchError,Workspace
from .pdf_dialogs import heading
from .theme import semantic_label


class BookmarksDialog(QDialog):
    def __init__(self,workspace,parent=None,*,page_uid=None):
        super().__init__(parent)
        self.result_bookmarks=None;self._uid=None;self._loading=False
        self.workspace=Workspace();self.workspace.restore_state(workspace.snapshot(include_history=False))
        self.active_page=page_uid if page_uid in {p.uid for p in self.workspace.pages} else (self.workspace.pages[0].uid if self.workspace.pages else None)
        self.setWindowTitle("Lesezeichen bearbeiten");self.resize(940,640);self.setMinimumSize(800,560)
        layout=QVBoxLayout(self);layout.setContentsMargins(22,20,22,18);layout.setSpacing(12)
        heading(layout,"Ein Kapitel. Immer die richtige Seite.","Lesezeichen bleiben beim Sortieren mit ihrer Arbeitsseite verbunden. Unterkapitel lassen sich mit den Schaltflächen oder Alt + Pfeiltasten verschieben.")
        tools=QHBoxLayout();self.add_button=QPushButton("Neues Lesezeichen");self.child_button=QPushButton("Unterkapitel");self.delete_button=QPushButton("Löschen")
        self.add_button.clicked.connect(self.add_bookmark);self.child_button.clicked.connect(lambda:self.add_bookmark(child=True));self.delete_button.clicked.connect(self.delete_bookmark)
        for button in (self.add_button,self.child_button,self.delete_button):tools.addWidget(button)
        tools.addStretch();self.undo_button=QPushButton("Rückgängig");self.redo_button=QPushButton("Wiederholen")
        self.undo_button.clicked.connect(self.undo);self.redo_button.clicked.connect(self.redo);tools.addWidget(self.undo_button);tools.addWidget(self.redo_button);layout.addLayout(tools)
        split=QSplitter();left=QWidget();column=QVBoxLayout(left);column.setContentsMargins(0,0,0,0);column.setSpacing(9)
        self.tree=QTreeWidget();self.tree.setHeaderLabels(["Kapitel","Zielseite"]);self.tree.setColumnWidth(0,260);self.tree.setAlternatingRowColors(False);self.tree.setAnimated(False)
        self.tree.setMinimumWidth(300);self.tree.setAccessibleName("Lesezeichenhierarchie. Pfeiltasten navigieren; Alt und Pfeiltasten verschieben; F2 bearbeitet den Titel.")
        self.tree.currentItemChanged.connect(self._selected);self.tree.itemDoubleClicked.connect(lambda *_:self._edit_title());column.addWidget(self.tree,1)
        row=QHBoxLayout();self.up_button=QPushButton("Nach oben");self.down_button=QPushButton("Nach unten")
        self.up_button.clicked.connect(lambda:self.move(-1));self.down_button.clicked.connect(lambda:self.move(1));row.addWidget(self.up_button);row.addWidget(self.down_button);column.addLayout(row)
        row=QHBoxLayout();self.indent_button=QPushButton("Einrücken");self.outdent_button=QPushButton("Ausrücken")
        self.indent_button.clicked.connect(self.indent);self.outdent_button.clicked.connect(self.outdent);row.addWidget(self.indent_button);row.addWidget(self.outdent_button);column.addLayout(row)
        split.addWidget(left);right=QWidget();right.setMinimumWidth(280);properties=QVBoxLayout(right);properties.setContentsMargins(14,0,0,0);properties.setSpacing(12)
        caption=QLabel("Ausgewähltes Lesezeichen");caption.setObjectName("sectionTitle");properties.addWidget(caption)
        form=QFormLayout();form.setSpacing(12);form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.title_edit=QLineEdit();self.title_edit.setMaxLength(500);self.title_edit.setAccessibleName("Lesezeichentitel")
        self.target_combo=QComboBox();self.target_combo.setAccessibleName("Zielseite des Lesezeichens");self.target_combo.addItem("Kapitel ohne eigenes Seitenziel",None)
        for index,page in enumerate(self.workspace.pages):
            source=self.workspace.sources[page.source_id];self.target_combo.addItem(f"Seite {index+1} · {source.label}",page.uid)
        form.addRow("Titel",self.title_edit);form.addRow("Ziel",self.target_combo);properties.addLayout(form)
        self.apply_button=QPushButton("Änderung übernehmen");self.apply_button.clicked.connect(self.apply_properties);properties.addWidget(self.apply_button)
        self.empty_hint=QLabel("Ein Kapitel kann Unterkapitel enthalten und selbst ohne Seitenziel bleiben. Wird eine Zielseite gelöscht, entfallen ihre Lesezeichen; vorhandene Unterkapitel bleiben als Gruppe erhalten.")
        self.empty_hint.setWordWrap(True);self.empty_hint.setObjectName("muted");properties.addWidget(self.empty_hint)
        self.error=QLabel();self.error.setWordWrap(True);semantic_label(self.error,"error");properties.addWidget(self.error);properties.addStretch()
        info=QLabel("PDF-Export: interne Seitenziele mit „Seite einpassen“. Externe Links, Programmstarts und Skriptaktionen werden als Lesezeichen nicht übernommen.")
        info.setWordWrap(True);info.setObjectName("muted");properties.addWidget(info);split.addWidget(right);split.setSizes([460,390]);split.setStretchFactor(0,1);layout.addWidget(split,1)
        warnings=tuple(dict.fromkeys(warning for source in self.workspace.sources.values() for warning in source.bookmark_warnings))
        self.import_hint=QLabel("\n".join(warnings));self.import_hint.setWordWrap(True);self.import_hint.setObjectName("muted");self.import_hint.setMaximumHeight(72);self.import_hint.setToolTip("\n".join(warnings));self.import_hint.setVisible(bool(warnings));layout.addWidget(self.import_hint)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen");buttons.button(QDialogButtonBox.StandardButton.Save).setText("Lesezeichen übernehmen");buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        for keys,callback in (("Alt+Up",lambda:self.move(-1)),("Alt+Down",lambda:self.move(1)),("Alt+Right",self.indent),("Alt+Left",self.outdent),("F2",self._edit_title)):
            shortcut=QShortcut(QKeySequence(keys),self);shortcut.activated.connect(callback)
        remove=QShortcut(QKeySequence("Delete"),self.tree);remove.setContext(Qt.ShortcutContext.WidgetShortcut);remove.activated.connect(self.delete_bookmark)
        self._refresh()

    def _current(self):return bookmark_location(self.workspace.bookmarks,self._uid) if self._uid else None

    def _refresh(self):
        self.tree.blockSignals(True);self.tree.clear();self._items={}
        indices={page.uid:index+1 for index,page in enumerate(self.workspace.pages)}
        def add(items,parent=None):
            for item in items:
                row=QTreeWidgetItem([item.title,str(indices[item.page_uid]) if item.page_uid else "Kapitel"]);row.setData(0,Qt.ItemDataRole.UserRole,item.uid);row.setToolTip(0,item.title)
                (parent.addChild(row) if parent else self.tree.addTopLevelItem(row));self._items[item.uid]=row;add(item.children,row);row.setExpanded(True)
        add(self.workspace.bookmarks)
        if self._uid not in self._items:self._uid=next(iter(self._items),None)
        if self._uid:self.tree.setCurrentItem(self._items[self._uid])
        self.tree.blockSignals(False);self._load();self._buttons()

    def _load(self):
        self._loading=True;found=self._current()
        self.title_edit.setText(found[2].title if found else "");self.target_combo.setCurrentIndex(self.target_combo.findData(found[2].page_uid) if found else 0)
        for control in (self.title_edit,self.target_combo,self.apply_button):control.setEnabled(found is not None)
        self._loading=False

    def _buttons(self):
        found=self._current();enabled=found is not None
        self.child_button.setEnabled(enabled);self.delete_button.setEnabled(enabled)
        self.undo_button.setEnabled(self.workspace.can_undo);self.redo_button.setEnabled(self.workspace.can_redo)
        siblings=(bookmark_location(self.workspace.bookmarks,found[0])[2].children if found and found[0] else self.workspace.bookmarks)
        self.up_button.setEnabled(enabled and found[1]>0);self.down_button.setEnabled(enabled and found[1]<len(siblings)-1)
        self.indent_button.setEnabled(enabled and found[1]>0);self.outdent_button.setEnabled(enabled and found[0] is not None)

    def _selected(self,item,previous):
        if item is None:return
        uid=item.data(0,Qt.ItemDataRole.UserRole)
        if self._uid and uid!=self._uid and not self.apply_properties(refresh=False):
            self.tree.blockSignals(True);self.tree.setCurrentItem(self._items[self._uid]);self.tree.blockSignals(False);return
        self._uid=uid;self._load();self._buttons()

    def select_bookmark(self,uid):
        item=self._items.get(uid)
        if item:self.tree.setCurrentItem(item)

    def apply_properties(self,*,refresh=True):
        if self._loading or self._current() is None:return True
        try:
            self.workspace.update_bookmark(self._uid,title=self.title_edit.text(),page_uid=self.target_combo.currentData())
            self.error.clear()
            if refresh:self._refresh()
            elif self._uid in self._items:
                item=self._items[self._uid];field=self._current()[2];item.setText(0,field.title)
                item.setText(1,str(next(index+1 for index,page in enumerate(self.workspace.pages) if page.uid==field.page_uid)) if field.page_uid else "Kapitel")
            return True
        except PdfWorkbenchError as error:self.error.setText(str(error));self.title_edit.setFocus();return False

    def add_bookmark(self,child=False):
        def add():
            found=self._current();parent=self._uid if child else found[0] if found else None
            self._uid=self.workspace.add_bookmark("Neues Unterkapitel" if child else "Neues Lesezeichen",self.active_page,parent_uid=parent)
        if not self._change(add):return None
        self._edit_title();return self._uid

    def _change(self,operation,*,apply=True):
        """Keep properties and a structural edit atomic, including their history."""
        before=self.workspace.snapshot();uid=self._uid
        title,target=self.title_edit.text(),self.target_combo.currentData()
        if apply and not self.apply_properties(refresh=False):return False
        try:operation()
        except PdfWorkbenchError as error:
            self.workspace.restore_state(before);self._uid=uid;self._refresh()
            # Keep the user's uncommitted properties available for correction.
            self.title_edit.setText(title);self.target_combo.setCurrentIndex(self.target_combo.findData(target))
            self.error.setText(str(error));return False
        self.error.clear();self._refresh();return True

    def delete_bookmark(self):
        if self._uid:
            def remove():self.workspace.delete_bookmark(self._uid);self._uid=None
            self._change(remove,apply=False)

    def move(self,direction):
        def move():
            found=self._current()
            if found is None:return
            parent,index,item=found;siblings=bookmark_location(self.workspace.bookmarks,parent)[2].children if parent else self.workspace.bookmarks
            target=index+direction
            if 0<=target<len(siblings):self.workspace.move_bookmark(item.uid,parent_uid=parent,index=target)
        self._change(move)

    def indent(self):
        def indent():
            found=self._current()
            if found is None or found[1]==0:return
            parent,index,item=found;siblings=bookmark_location(self.workspace.bookmarks,parent)[2].children if parent else self.workspace.bookmarks
            self.workspace.move_bookmark(item.uid,parent_uid=siblings[index-1].uid)
        self._change(indent)

    def outdent(self):
        def outdent():
            found=self._current()
            if found is None or found[0] is None:return
            parent=bookmark_location(self.workspace.bookmarks,found[0]);self.workspace.move_bookmark(self._uid,parent_uid=parent[0],index=parent[1]+1)
        self._change(outdent)

    def _edit_title(self):
        if self._uid:self.title_edit.setFocus();self.title_edit.selectAll()

    def undo(self):self.workspace.undo();self.error.clear();self._refresh()
    def redo(self):self.workspace.redo();self.error.clear();self._refresh()

    def accept(self):
        if not self.apply_properties():return
        try:validate_bookmarks(self.workspace.bookmarks,{page.uid for page in self.workspace.pages})
        except PdfWorkbenchError as error:self.error.setText(str(error));return
        self.result_bookmarks=self.workspace.bookmarks;super().accept()

    @classmethod
    def get_bookmarks(cls,parent,workspace,*,page_uid=None):
        dialog=cls(workspace,parent,page_uid=page_uid)
        try:return dialog.result_bookmarks if dialog.exec()==QDialog.DialogCode.Accepted else None
        finally:dialog.deleteLater()
