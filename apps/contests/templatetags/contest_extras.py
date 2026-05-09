import json
from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    if hasattr(dictionary, 'get'):
        return dictionary.get(key)
    return None


from django.utils.safestring import mark_safe

@register.filter
def tojson(value):
    """Safely serialize a value to a JSON string for embedding in JS."""
    return mark_safe(json.dumps(value if value is not None else ''))


@register.simple_tag
def set_var(value):
    return value


@register.filter
def startswith(value, prefix):
    """Check if a string starts with the given prefix."""
    if isinstance(value, str):
        return value.startswith(prefix)
    return False