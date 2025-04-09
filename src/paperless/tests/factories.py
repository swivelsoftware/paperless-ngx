from factory import Faker
from factory.django import DjangoModelFactory

from paperless.models import Correspondent
from paperless.models import Document


class CorrespondentFactory(DjangoModelFactory):
    class Meta:
        model = Correspondent

    name = Faker("name")


class DocumentFactory(DjangoModelFactory):
    class Meta:
        model = Document
