from django.db.models.base import ModelBase
from django.db.models import ForeignKey, Field
from django.db.models.fields.related import ReverseSingleRelatedObjectDescriptor
from django.db.models.fields.related import RelatedField
from django.db.models.fields.related import ForeignRelatedObjectsDescriptor
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core import serializers
from django.forms import ModelChoiceField, Select

from fossil.models import Fossil

# ------- MODELS -------

class FossilForeignKey(ForeignKey):
    def __init__(self, to, **kwargs):
        """Here we must to store indirect object type to use it later"""

        # Stores indirect model classe relationship
        self.indirect_to = to
        self.indirect_kwargs = {
            'db_index': kwargs.pop('db_index', None),
            }

        super(FossilForeignKey, self).__init__(Fossil, **kwargs)

    def contribute_to_class(self, cls, name):
        super(FossilForeignKey, self).contribute_to_class(cls, name)

        # Replaces field descriptor
        setattr(cls, self.name, FossilSingleObjectDescriptor(self))

    @property
    def indirect_content_type(self):
        if not getattr(self, '_indirect_content_type', None):
            self._indirect_content_type = ContentType.objects.get_for_model(self.indirect_to)
        return self._indirect_content_type

    def pre_save(self, model_instance, add):
        try:
            obj = model_instance._indirect_references.get(self, None)
        except AttributeError:
            obj = None

        if not obj: return None

        # Gets or creates a fossil for current version of the related object
        fossil = Fossil.objects.create_for_object(obj)

        # Returns fossil's id
        return fossil.pk

    def formfield(self, **kwargs):
        defaults = {
                'form_class': FossilChoiceField,
                'indirect_to': self.indirect_to,
                }
        defaults.update(kwargs)

        return super(FossilForeignKey, self).formfield(**defaults)

    def validate(self, value, model_instance):
        # IMPORTANT: This must be to ForeignKey's superclass to ignore its own override
        super(ForeignKey, self).validate(value, model_instance)

        if value is None:
            return

        # This is really seemed to ForeignKey validation, but makes from indirect model class (not fossil revisions)
        qs = self.indirect_to._default_manager.filter(**{self.rel.field_name:value})
        qs = qs.complex_filter(self.rel.limit_choices_to)
        if not qs.exists():
            raise ValidationError(self.error_messages['invalid'] % {
                'model': self.rel.to._meta.verbose_name, 'pk': value})

    def contribute_to_related_class(self, cls, related):
        """This method is based on ForeignKey's method with same name, but have to be
        here to override it and implement the relationship direct to related class,
        instead of Fossil."""

        cls = self.indirect_to

        # Internal FK's - i.e., those with a related name ending with '+' -
        # don't get a related descriptor.
        if not self.rel.is_hidden():
            setattr(cls, related.get_accessor_name(), FossilRelatedObjectDescriptor(related))
        if self.rel.field_name is None:
            self.rel.field_name = cls._meta.pk.name

class FossilProxy(object):
    fossil = None

    def __init__(self, fossil=None):
        self.fossil = fossil

    @property
    def object_fossil(self):
        if not hasattr(self, '_object_fossil'):
            self._object_fossil = self.fossil.get_object_fossil()

        return self._object_fossil

    def __getattr__(self, name):
        if name in ('_object_fossil','fossil'):
            raise AttributeError

        return getattr(self.object_fossil.object, name)

    def __repr__(self):
        return '<%s: %s #%s>'%(self.__class__.__name__, self.fossil.content_type.model, self.object_fossil.object.pk)

    def __unicode__(self):
        return unicode(self.fossil)

    def __str__(self):
        return str(self.fossil)

class FossilSingleObjectDescriptor(ReverseSingleRelatedObjectDescriptor):
    """Take more details from the superclass."""

    def __init__(self, field_with_rel):
        self.field = field_with_rel    

    def __get__(self, instance, instance_type=None):
        if instance is None:
            return self

        cache_name = self.field.get_cache_name()
        
        try:
            value = getattr(instance, cache_name)
        except AttributeError:
            value = Fossil.objects.get(
                    pk=getattr(instance, self.field.attname)
                    )

        if isinstance(value, Fossil):
            value = FossilProxy(value)
        
        return value
    
    def __set__(self, instance, value):
        """Here we have to store the referenced object in a temporary attribute of
        instance and set a temporary Fossil instance to the field."""

        instance._indirect_references = getattr(instance, '_indirect_references', {})
        instance._indirect_references[self.field] = value

        if value:
            setattr(instance, self.field.attname, value.pk)
            setattr(instance, self.field.get_cache_name(), value)
        else:
            setattr(instance, self.field.attname, None)
            setattr(instance, self.field.get_cache_name(), None)

class FossilRelatedObjectDescriptor(ForeignRelatedObjectsDescriptor):
    def create_manager(self, instance, superclass):
        """This function is not so full as Django does. If we have necessity we can enhance
        it to support creating and clearing objects."""

        rel_field = self.related.field
        rel_model = self.related.model

        conditions = {
                '%s__content_type' % rel_field.name: rel_field.indirect_content_type,
                '%s__object_id' % rel_field.name: instance.pk,
                }

        queryset = rel_model._default_manager.filter(**conditions)

        return queryset

# ------- FORMS -------

class FossilChoiceWidget(Select):
    """Form Widget class mostly used by FossilForeignKey fields."""

    def render(self, name, value, *args, **kwargs):
        # Swap initial value to pk of real reference object
        if value:
            fossil = Fossil.objects.filter(pk=value).values_list('object_id', flat=True)[0]
            value = fossil[0]

        return super(FossilChoiceWidget, self).render(name, value, *args, **kwargs)

class FossilChoiceField(ModelChoiceField):
    """Form Field class mostly used by FossilForeignKey fields."""

    widget = FossilChoiceWidget
    indirect_to = None

    def __init__(self, queryset, *args, **kwargs):
        self.indirect_to = kwargs.pop('indirect_to', None)

        # Swap Fossil queryset for foreign model class queryset
        if self.indirect_to:
            queryset = self.indirect_to.objects.all()
            if self.indirect_to._meta.ordering:
                queryset = queryset.order_by(*self.indirect_to._meta.ordering)
            else:
                queryset = queryset.order_by('pk')
        else:
            queryset = None

        super(FossilChoiceField, self).__init__(queryset, *args, **kwargs)


