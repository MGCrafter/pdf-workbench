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
