from django import forms
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html

from .forms import CalculationForm
from .models import Artwork, Calendar

admin.site.site_header = "Stringy · Atelier"
admin.site.site_title = "Stringy"
admin.site.index_title = "Verwaltung"


class ArtworkForm(CalculationForm):
    photo = forms.ImageField(
        label="Bilddatei", help_text="JPEG, PNG oder WebP, maximal 10 MiB und 8192 Pixel je Seite."
    )

    def clean_photo(self):
        photo = self.cleaned_data["photo"]
        if (
            photo.image.format not in {"JPEG", "PNG", "WEBP"}
            or photo.size > 10 * 1024**2
            or max(photo.image.size) > 8192
        ):
            raise forms.ValidationError(
                "Bitte ein JPEG, PNG oder WebP innerhalb der angegebenen Grenzen wählen."
            )
        return photo


@admin.register(Artwork)
class ArtworkAdmin(admin.ModelAdmin):
    list_display = ("title", "pk", "state", "count", "simulation")
    list_filter = ("state",)
    actions = ("retry_failed",)

    def get_queryset(self, request):
        return super().get_queryset(request).defer("source", "preview", "target", "route")

    @admin.action(description="Fehlgeschlagene Motive erneut berechnen", permissions=["change"])
    def retry_failed(self, request, queryset):
        count = queryset.filter(state=Artwork.State.FAILED).update(
            state=Artwork.State.QUEUED, claimed_at=None, error=""
        )
        self.message_user(request, f"{count} Motive zur Berechnung eingereiht.")

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = ArtworkForm
        return super().get_form(request, obj, **kwargs)

    def get_fields(self, request, obj=None):
        if obj:
            return (
                "title",
                "state",
                "count",
                "anchors",
                "diameter",
                "thread",
                "budget",
                "passes",
                "detail",
                "opacity",
                "zoom",
                "center_x",
                "center_y",
                "gamma",
                "simulation",
                "error",
            )
        return ("title", "photo", *CalculationForm.Meta.fields[1:])

    def get_readonly_fields(self, request, obj=None):
        return self.get_fields(request, obj)[1:] if obj else ()

    @admin.display(description="Vorschau")
    def simulation(self, obj):
        if obj.state == Artwork.State.READY:
            return format_html(
                '<a href="{}">Simulation öffnen</a>', reverse("simulation", args=[obj.pk])
            )
        return "–"

    def response_add(self, request, obj, post_url_continue=None):
        if "_save" in request.POST and self.has_view_permission(request, obj):
            return redirect("simulation", pk=obj.pk)
        return super().response_add(request, obj, post_url_continue)

    def save_model(self, request, obj, form, change):
        if change:
            obj.save(update_fields=["title"])
        else:
            obj.source = form.cleaned_data["photo"].read()
            super().save_model(request, obj, form, change)


@admin.register(Calendar)
class CalendarAdmin(admin.ModelAdmin):
    list_display = ("title", "recipient", "year", "artwork", "finished")
    list_filter = ("year",)
    fields = ("title", "recipient", "year", "artwork")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "artwork":
            kwargs["queryset"] = Artwork.objects.only("pk", "title")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("artwork", "recipient")
            .defer("artwork__source", "artwork__route", "artwork__target", "artwork__preview")
        )

    def get_readonly_fields(self, request, obj=None):
        return ("recipient", "year", "artwork") if obj and obj.unlocked() else ()
