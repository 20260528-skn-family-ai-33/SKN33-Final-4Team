from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("finance", "0009_merchant_embedding")]

    operations = [
        migrations.AddField(
            model_name="card",
            name="first_connected_at",
            field=models.DateTimeField(
                "최초 카드 연결 시각", null=True, blank=True, editable=False,
            ),
        ),
    ]
