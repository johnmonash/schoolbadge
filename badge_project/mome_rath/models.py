import logging
import os.path
import random
import hashlib
from urlparse import urljoin
from time import time

from django.db import models
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db.models import signals, Q, Count, Max
from django.contrib.auth.models import User as UserModel
from django.core.urlresolvers import reverse
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.template.defaultfilters import slugify


try:
    from tower import ugettext_lazy as _
except ImportError:
    from django.utils.translation import ugettext_lazy as _

try:
    from django.contrib.auth import get_user_model
except ImportError:  # django <= 1.4 doesn't have get_user_model(); define our own and return the stock User class
    def get_user_model():
        return UserModel
User = getattr(settings, 'AUTH_USER_MODEL', 'auth.User')  # Field definitions should use this

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO

try:
    from PIL import Image
except ImportError:
    import Image

try:
    import taggit
    from taggit.managers import TaggableManager
    from taggit.models import Tag, TaggedItem
except ImportError:
    taggit = None


IMG_MAX_SIZE = getattr(settings, "BADGER_IMG_MAX_SIZE", (256, 256))
DEFAULT_HTTP_PROTOCOL = getattr(settings, "DEFAULT_HTTP_PROTOCOL", "http")
UPLOADS_ROOT = getattr(settings, 'BADGER_MEDIA_ROOT',
    os.path.join(getattr(settings, 'MEDIA_ROOT', 'media/'), 'uploads'))
UPLOADS_URL = getattr(settings, 'BADGER_MEDIA_URL',
    urljoin(getattr(settings, 'MEDIA_URL', '/media/'), 'uploads/'))
BADGE_UPLOADS_FS = FileSystemStorage(location=UPLOADS_ROOT, base_url=UPLOADS_URL)
MK_UPLOAD_TMPL = '%(base)s/%(h1)s/%(h2)s/%(hash)s_%(field_fn)s_%(now)s_%(rand)04d.%(ext)s'

def scale_image(img_upload, img_max_size):
    """Crop and scale an image file."""
    try:
        img = Image.open(img_upload)
    except IOError:
        return None

    src_width, src_height = img.size
    src_ratio = float(src_width) / float(src_height)
    dst_width, dst_height = img_max_size
    dst_ratio = float(dst_width) / float(dst_height)

    if dst_ratio < src_ratio:
        crop_height = src_height
        crop_width = crop_height * dst_ratio
        x_offset = int(float(src_width - crop_width) / 2)
        y_offset = 0
    else:
        crop_width = src_width
        crop_height = crop_width / dst_ratio
        x_offset = 0
        y_offset = int(float(src_height - crop_height) / 2)

    img = img.crop((x_offset, y_offset,
                    x_offset + int(crop_width), y_offset + int(crop_height)))
    img = img.resize((dst_width, dst_height), Image.ANTIALIAS)

    # If the mode isn't RGB or RGBA we convert it. If it's not one
    # of those modes, then we don't know what the alpha channel should
    # be so we convert it to "RGB".
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    new_img = StringIO()
    img.save(new_img, "PNG")
    img_data = new_img.getvalue()

    return ContentFile(img_data)


def mk_upload_to(field_fn, ext, tmpl=MK_UPLOAD_TMPL):
    """upload_to builder for file upload fields"""
    def upload_to(instance, filename):
        base, slug = instance.get_upload_meta()
        slug_hash = (hashlib.md5(slug.encode('utf-8', 'ignore'))
                            .hexdigest())
        return tmpl % dict(now=int(time()), rand=random.randint(0, 1000),
                           slug=slug[:50], base=base, field_fn=field_fn,
                           pk=instance.pk,
                           hash=slug_hash, h1=slug_hash[0], h2=slug_hash[1],
                           ext=ext)
    return upload_to


class BadgerException(Exception):
    """General Badger model exception"""


class BadgeException(BadgerException):
    """Badge model exception"""


class BadgeAwardNotAllowedException(BadgeException):
    """Attempt to award a badge not allowed."""


class BadgeAlreadyAwardedException(BadgeException):
    """Attempt to award a badge that has already been awarded."""


class BadgeManager(models.Manager):
    """Manager for Badge model objects"""
    search_fields = ('title', 'slug', 'description', )

    def allows_add_by(self, user):
        if user.is_anonymous():
            return False
        try:
            creator_badge = Badge.objects.get(slug='creator') #TODO - use settings
            return creator_badge.is_awarded_to(user)
        except Badge.DoesNotExist:
            return False
        return False


class Badge(models.Model):
    """Representation of a badge"""
    objects = BadgeManager()

    title = models.CharField(max_length=255, blank=False, unique=True, help_text='Short, descriptive title')
    slug = models.SlugField(blank=False, unique=True, help_text='Very short name, for use in URLs and links')
    description = models.TextField(blank=True, help_text='Longer description of the badge and its criteria')
    image = models.ImageField(blank=True, null=True,
                              storage=BADGE_UPLOADS_FS, upload_to=mk_upload_to('image', 'png'),
                              help_text='Upload an image to represent the badge')
    prerequisites = models.ManyToManyField('self', symmetrical=False,
                                           blank=True, null=True, related_name='prereq',
                                           help_text=('When all of the selected badges have been awarded, this '
                                                      'badge will be automatically awarded.'))
    awarding_prerequisite = models.ForeignKey('self', blank=True, null=True, related_name='awarding_prereq',
                                              help_text=('In order to award the current badge, the awarder must '
                                                         'hold the selected badge'))
    nominations_accepted = models.BooleanField(default=True, blank=True,
                                               help_text=('Should this badge accept nominations from '
                                                          'other users?'))

    if taggit:
        tags = TaggableManager(blank=True)

    creator = models.ForeignKey(User, blank=True, null=True)
    created = models.DateTimeField(auto_now_add=True, blank=False)
    modified = models.DateTimeField(auto_now=True, blank=False)

    class Meta:
        unique_together = ('title', 'slug')
        ordering = ['-modified', '-created']
        permissions = (
            ('manage_deferredawards',
             _('Can manage deferred awards for this badge')),
        )


    def __unicode__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('mome_rath.badge_detail', args=(self.slug,))

    def get_upload_meta(self):
        return ("badge", self.slug)

    def clean(self):
        if self.image:
            scaled_file = scale_image(self.image.file, IMG_MAX_SIZE)
            if not scaled_file:
                raise ValidationError(_('Cannot process image'))
            self.image.file = scaled_file

    def save(self, **kwargs):
        """Save the submission, updating slug"""
        if not self.slug:
            self.slug = slugify(self.title)
        #TODO - clean/process image?
        super(Badge, self).save(**kwargs)

    def delete(self, **kwargs):
        """Make sure deletes cascade to awards"""
        self.award_set.all().delete()
        super(Badge, self).delete(**kwargs)

    def allows_award_to(self, user):
        """Is the current user allowed to award this badge?"""
        if not user or user.is_anonymous():
            return False
        if user.is_superuser:
            return True
        if self.awarding_prerequisite and self.awarding_prerequisite.is_awarded_to(user):
            return True
        return False

    def award_to(self, awardee, awarder, description='', raise_already_awarded=False):
        if not self.allows_award_to(awarder):
            raise BadgeAwardNotAllowedException
        if self.is_awarded_to(awardee):
            if raise_already_awarded:
                raise BadgeAlreadyAwardedException
            else:
                return Award.objects.get(user=awardee, badge=self)
        return Award.objects.create(user=awardee, badge=self, creator=awarder, description=description)

    def is_awarded_to(self, user):
        return Award.objects.filter(user=user, badge=self).count() > 0


class AwardManager(models.Manager):
    pass


class Award(models.Model):
    """Representation of a badge awarded to a user"""

    admin_objects = models.Manager()
    objects = AwardManager()

    description = models.TextField(blank=True,
            help_text='Explanation and evidence for the badge award')
    badge = models.ForeignKey(Badge)
    image = models.ImageField(blank=True, null=True,
                              storage=BADGE_UPLOADS_FS,
                              upload_to=mk_upload_to('image', 'png'))
    user = models.ForeignKey(User, related_name="awardee")
    creator = models.ForeignKey(User, related_name="awarder",
                                blank=True, null=True)
    created = models.DateTimeField(auto_now_add=True, blank=False)
    modified = models.DateTimeField(auto_now=True, blank=False)

    class Meta:
        ordering = ['-modified', '-created']

    def __unicode__(self):
        by = self.creator and (u' by %s' % self.creator) or u''
        return u'Award of %s to %s%s' % (self.badge, self.user, by)

#    @models.permalink
    def get_absolute_url(self):
        return reverse('mome_rath.award_detail', kwargs={'slug': self.badge.slug, 'id': self.pk})

    def get_upload_meta(self):
        u = self.user.username
        return ("award/%s/%s/%s" % (u[0], u[1], u), self.badge.slug)


