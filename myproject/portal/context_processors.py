"""Context processors available to every template."""


def user_initials(request):
    """The avatar initials for the logged-in user, computed ONE way for
    every page: initials of the full name if set, else the first two
    characters of the username. (Pages used to each roll their own —
    the dashboard showed name initials while the complaints page showed
    the first two characters of the matric-number username.)"""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"initials": ""}
    full_name = user.get_full_name()
    if full_name:
        parts = full_name.split()[:2]
        return {"initials": "".join(p[0].upper() for p in parts)}
    return {"initials": user.username[:2].upper()}
