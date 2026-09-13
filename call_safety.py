ASSESSMENT_DESTINATION = "+18054398008"


def validate_destination(number):
    """Return only the exact approved destination; reject all other inputs."""
    if number != ASSESSMENT_DESTINATION:
        raise ValueError("Destination must exactly match the assessment destination.")
    return number
