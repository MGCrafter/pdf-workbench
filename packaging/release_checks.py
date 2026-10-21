"""Named acceptance requirements shared by the native smoke and release gate."""

V06_CORE_CHECKS = frozenset((
    "test_aes256_and_image_compression_runtime",
    "test_acroform_fill_export_reopen_runtime",
    "test_password_required_and_orphaned_widget_rejected",
))

V06_GUI_CHECKS = frozenset((
    "password import retry/cancel and AES export",
    "compression preview and measured export",
    "AcroForm edit/export/reopen/undo",
    "reduced motion and compact themes",
))

V07_CORE_CHECKS = frozenset((
    "test_document_comparison_runtime",
    "test_form_design_radio_runtime",
    "test_page_marks_selected_copy_runtime",
))

V07_GUI_CHECKS = frozenset((
    "PDF comparison text/visual alignment and read-only navigation",
    "AcroForm design create/edit/delete/radio and undo",
    "page marks preview/export/reopen/selection and undo",
))
