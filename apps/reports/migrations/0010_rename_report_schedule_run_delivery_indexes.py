from __future__ import annotations

from django.db import migrations, models


def _rename_index_sql(old_name: str, new_name: str) -> str:
    return f"""
DO $$
BEGIN
    IF to_regclass('public.{old_name}') IS NOT NULL AND to_regclass('public.{new_name}') IS NULL THEN
        EXECUTE 'ALTER INDEX "{old_name}" RENAME TO "{new_name}"';
    END IF;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0009_reportschedulerun_reportscheduledelivery"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(_rename_index_sql("report_sch_del_run_idx", "report_sche_run_id_945b96_idx")),
                migrations.RunSQL(_rename_index_sql("report_sch_del_chan_idx", "report_sche_channel_2c9579_idx")),
                migrations.RunSQL(_rename_index_sql("report_sch_del_stat_idx", "report_sche_status_6e1826_idx")),
                migrations.RunSQL(_rename_index_sql("report_sch_del_next_idx", "report_sche_next_re_e74d05_idx")),
                migrations.RunSQL(_rename_index_sql("report_sch_del_crea_idx", "report_sche_created_8c167a_idx")),
                migrations.RunSQL(_rename_index_sql("report_sch_del_sent_idx", "report_sche_sent_at_4b3f62_idx")),
                migrations.RunSQL(_rename_index_sql("report_sch_run_sched_idx", "report_sche_schedul_e2635a_idx")),
                migrations.RunSQL(_rename_index_sql("report_sch_run_stat_idx", "report_sche_status_724f9e_idx")),
                migrations.RunSQL(_rename_index_sql("report_sch_run_queued_idx", "report_sche_queued__614c44_idx")),
                migrations.RunSQL(_rename_index_sql("report_sch_run_start_idx", "report_sche_started_44e31a_idx")),
                migrations.RunSQL(_rename_index_sql("report_sch_run_comp_idx", "report_sche_complet_715c1a_idx")),
                migrations.RunSQL(_rename_index_sql("report_sch_run_crea_idx", "report_sche_created_90b8ee_idx")),
            ],
            state_operations=[
                migrations.RenameIndex(
                    model_name="reportscheduledelivery",
                    old_name="report_sch_del_run_idx",
                    new_name="report_sche_run_id_945b96_idx",
                ),
                migrations.RenameIndex(
                    model_name="reportscheduledelivery",
                    old_name="report_sch_del_chan_idx",
                    new_name="report_sche_channel_2c9579_idx",
                ),
                migrations.RenameIndex(
                    model_name="reportscheduledelivery",
                    old_name="report_sch_del_stat_idx",
                    new_name="report_sche_status_6e1826_idx",
                ),
                migrations.RenameIndex(
                    model_name="reportscheduledelivery",
                    old_name="report_sch_del_next_idx",
                    new_name="report_sche_next_re_e74d05_idx",
                ),
                migrations.RenameIndex(
                    model_name="reportscheduledelivery",
                    old_name="report_sch_del_crea_idx",
                    new_name="report_sche_created_8c167a_idx",
                ),
                migrations.RenameIndex(
                    model_name="reportscheduledelivery",
                    old_name="report_sch_del_sent_idx",
                    new_name="report_sche_sent_at_4b3f62_idx",
                ),
                migrations.RenameIndex(
                    model_name="reportschedulerun",
                    old_name="report_sch_run_sched_idx",
                    new_name="report_sche_schedul_e2635a_idx",
                ),
                migrations.RenameIndex(
                    model_name="reportschedulerun",
                    old_name="report_sch_run_stat_idx",
                    new_name="report_sche_status_724f9e_idx",
                ),
                migrations.RenameIndex(
                    model_name="reportschedulerun",
                    old_name="report_sch_run_queued_idx",
                    new_name="report_sche_queued__614c44_idx",
                ),
                migrations.RenameIndex(
                    model_name="reportschedulerun",
                    old_name="report_sch_run_start_idx",
                    new_name="report_sche_started_44e31a_idx",
                ),
                migrations.RenameIndex(
                    model_name="reportschedulerun",
                    old_name="report_sch_run_comp_idx",
                    new_name="report_sche_complet_715c1a_idx",
                ),
                migrations.RenameIndex(
                    model_name="reportschedulerun",
                    old_name="report_sch_run_crea_idx",
                    new_name="report_sche_created_90b8ee_idx",
                ),
            ],
        ),
    ]
