from .models import UserPreferences


def preferences(request):
    if request.user.is_authenticated:
        prefs = UserPreferences.for_user(request.user)
        return {'prefs': prefs}
    return {'prefs': None}
