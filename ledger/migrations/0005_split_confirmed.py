from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0004_price_split_quantity_num_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='split',
            name='confirmed',
            field=models.BooleanField(default=False),
        ),
    ]
