__author__ = 'Victor Rajewski'

from django import forms

from .models import Badge


class BadgeEditForm(forms.ModelForm):
    class Meta:
        model = Badge
        fields = ('title', 'image', 'description', 'prerequisites', 'awarding_prerequisite')


class BadgeCreateForm(BadgeEditForm):
    class Meta(BadgeEditForm.Meta):
        pass
        #fields = BadgeEditForm.Meta.fields + ('creator',)
