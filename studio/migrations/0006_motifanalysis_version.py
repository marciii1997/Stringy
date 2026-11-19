from typing import ClassVar

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies: ClassVar = [("studio", "0005_alter_artwork_zoom")]
    operations: ClassVar = [
        migrations.AddField(
            model_name="motifanalysis",
            name="version",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AlterField(
            model_name="motifanalysis",
            name="version",
            field=models.PositiveSmallIntegerField(default=2),
        ),
    ]
