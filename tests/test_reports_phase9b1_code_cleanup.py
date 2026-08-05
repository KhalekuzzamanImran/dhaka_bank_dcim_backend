from __future__ import annotations

import importlib.util

from django.test import TestCase

from apps.reports.models import ReportSchedule


class ReportPhase9B1CodeCleanupTestCase(TestCase):
    def test_legacy_generator_wrapper_is_removed(self):
        self.assertIsNone(importlib.util.find_spec("apps.reports.services.generator"))

    def test_report_schedule_default_format_is_normalized(self):
        field = ReportSchedule._meta.get_field("output_format")
        self.assertEqual(field.default, "PDF")

    def test_report_schedule_format_choices_do_not_expose_pdf_csv(self):
        field = ReportSchedule._meta.get_field("output_format")
        choices = [choice[0] for choice in field.choices]
        self.assertNotIn("PDF_CSV", choices)
        self.assertEqual(sorted(choices), ["CSV", "PDF", "XLSX"])
