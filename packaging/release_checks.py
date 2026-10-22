"""Named acceptance requirements shared by the native smoke and release gate."""


V04_GUI_CHECKS = frozenset((
    "appearance dark/light/system", "appearance persisted",
    "appearance preserves document and viewport", "appearance document/export colors",
    "appearance dialogs and icons",
))

V05_GUI_CHECKS = frozenset((
    "objects select/move/duplicate/delete/undo", "objects text/image/stamp/symbol PDF roundtrip",
    "objects bundled font and Unicode", "asynchronous text search", "images to PDF and undo",
    "local OCR searchable text and preserved image",
))

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

V09_CORE_CHECKS = frozenset((
    "test_batch_recipe_order_rotation_originals_and_collisions",
    "test_batch_cancel_retains_foreign_edits_and_cleans_own_output",
    "test_image_text_exports_follow_final_selection_and_rotation",
    "test_blank_and_duplicate_are_review_candidates_without_mutation",
    "test_duplex_reverse_odd_fronts_and_one_undo",
    "test_scan_selected_page_does_not_change_unselected_page",
    "test_toc_and_links_follow_reordered_rotated_pages",
    "test_redaction_physically_removes_original_streams_metadata_and_annotations",
    "test_text_replacement_isolated_copy_rejects_overflow_and_undo",
    "test_signature_integrity_trust_separation_and_tamper",
    "test_pdfa_requires_actual_validator_and_preserves_order_orientation",
))

V09_GUI_CHECKS = frozenset((
    "tabs isolate project identity, zoom, history and reversible cross-tab move",
    "actual background job blocks tab switch/create/close",
    "failed update launch never reuses stale discard approval after edits",
    "cancel target-tab close retains pages; confirmed close removes only target",
    "existing-text real dialog edits isolated source and undo remains available",
    "redaction real preview, explicit review checkbox and physical removal export",
    "image and UTF8 text exports use real scope dialogs and jobs",
    "scan native settings, selected-page replacement and one undo",
    "duplex native preview and final back rotation",
    "manual blank/duplicate review starts unchecked and deletion has undo",
    "contents and internal-link actual dialogs create PDF navigation",
    "batch native files/options, remembered template and actual export",
    "certificate native password dialog, cleared secret and real PKCS12 signing",
    "signature native original-file verification and integrity/trust report",
    "native PDF/A dialog and actual Ghostscript plus veraPDF validated export",
))


def _version_pair(version):
    return tuple(int(part) for part in version.lstrip("v").split(".")[:2])


def required_core_checks(version):
    current = _version_pair(version)
    return frozenset().union(*(checks for minimum, checks in (
        ((0, 6), V06_CORE_CHECKS), ((0, 7), V07_CORE_CHECKS),
        ((0, 8), V08_CORE_CHECKS), ((0, 9), V09_CORE_CHECKS)) if current >= minimum))


def required_gui_checks(version):
    current = _version_pair(version)
    return frozenset().union(*(checks for minimum, checks in (
        ((0, 4), V04_GUI_CHECKS), ((0, 5), V05_GUI_CHECKS),
        ((0, 6), V06_GUI_CHECKS), ((0, 7), V07_GUI_CHECKS),
        ((0, 8), V08_GUI_CHECKS), ((0, 9), V09_GUI_CHECKS)) if current >= minimum))


def minimum_core_tests(version):
    current = _version_pair(version)
    return next(count for minimum, count in (
        ((0, 9), 34), ((0, 8), 23), ((0, 7), 18), ((0, 6), 15), ((0, 0), 13)) if current >= minimum)
