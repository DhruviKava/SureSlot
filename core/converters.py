# core/converters.py

from core.obfuscator import encode_id, decode_id

class ObfuscatedIDConverter:
    regex = '[a-z0-9]+'

    def to_python(self, value):
        try:
            return decode_id(value)
        except ValueError:
            raise ValueError("Invalid obfuscated ID")

    def to_url(self, value):
        if hasattr(value, 'pk'):
            value = value.pk
        # If already an encoded string (contains alphabetic characters)
        if isinstance(value, str) and not value.isdigit():
            return value
        try:
            return encode_id(int(value))
        except (ValueError, TypeError):
            return str(value)
