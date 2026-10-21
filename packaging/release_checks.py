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


V08_CORE_CHECKS = frozenset((
    "test_bookmarks_reorder_subset_runtime",
    "test_page_geometry_crop_resize_runtime",
    "test_project_history_encryption_runtime",
    "test_recovery_generations_runtime",
    "test_update_metadata_integrity_runtime",
))

V08_GUI_CHECKS = frozenset((
    "bookmarks edit/export/page targets and undo",
    "page geometry real preview/crop/resize/selection and undo",
    "project save/open/history/view and original independence",
    "autosave/recovery/encrypted snapshot and safe close",
    "updater fixture check/cancel/preferences without installation",
))
