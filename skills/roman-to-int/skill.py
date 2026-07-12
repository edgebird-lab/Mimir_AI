def roman_to_int(s):
    """
    Converts a Roman numeral string to an integer.
    """
    if not s:
        return 0
    
    roman_values = {
        'I': 1,
        'V': 5,
        'X': 10,
        'L': 50,
        'C': 100,
        'D': 500,
        'M': 1000
    }
    
    total = 0
    prev_value = 0
    
    # Process from right to left
    for char in reversed(s.upper()):
        if char not in roman_values:
            raise ValueError(f"Invalid Roman numeral character: {char}")
        
        value = roman_values[char]
        
        # If the current value is less than the previous one, subtract it
        if value < prev_value:
            total -= value
        else:
            total += value
        
        prev_value = value
    
    return total

# Read input from skill_input variable
roman_numeral = skill_input

# Convert and assign result
result = roman_to_int(roman_numeral)