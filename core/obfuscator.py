# core/obfuscator.py

MASK = 0x5E3A1C9F
MULT = 0x1E3D5B7
INV_MULT = 0xBF241807

def encode_id(n: int) -> str:
    """Obfuscates an integer ID into a safe, non-sequential alphanumeric string."""
    if not isinstance(n, int):
        try:
            n = int(n)
        except (ValueError, TypeError):
            return ""
    
    # Apply XOR and multiplicative obfuscation constrained to 32-bit unsigned
    obfuscated = ((n * MULT) & 0xFFFFFFFF) ^ MASK
    
    # Convert to Base36
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    result = []
    val = obfuscated & 0xFFFFFFFF
    while val > 0:
        result.append(chars[val % 36])
        val //= 36
    return "".join(reversed(result)) if result else chars[0]

def decode_id(s: str) -> int:
    """Decodes an obfuscated alphanumeric string back to its original integer ID."""
    if not s or not isinstance(s, str):
        raise ValueError("Invalid encoded ID format")
        
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    val = 0
    for char in s.lower():
        if char not in chars:
            raise ValueError("Invalid character in encoded ID")
        val = val * 36 + chars.index(char)
        
    # Reverse XOR and multiplicative obfuscation
    obfuscated = val ^ MASK
    n = (obfuscated * INV_MULT) & 0xFFFFFFFF
    return n
