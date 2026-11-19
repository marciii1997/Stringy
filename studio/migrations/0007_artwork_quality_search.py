from typing import ClassVar

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies: ClassVar = [("studio", "0006_motifanalysis_version")]
    operations: ClassVar = [
        migrations.AddField(
            model_name="artwork",
            name="quality_search",
            field=models.BooleanField(
                default=True, verbose_name="Qualitätsvergleich mit feinem Rechenraster"
            ),
        ),
    ]
