from django.db import models
from django.contrib.auth.models import User

# Create your models here.

class Group(models.Model):
	name = models.CharField(maxlength = 250)
	longname = models.CharField(maxlength = 250)
	default_theme = models.ForeignKey('Theme', null = True, blank = True)
	parent = models.ForeignKey('Group', null = True, blank = True)
        baseurl = models.CharField(maxlength = 250)

        def get_name(self):
                return self.longname or self.name

        def recursiveName(self):
                recname = ''
                if self.parent:
                        recname = self.parent.recursiveName() + ' / '
                return recname + self.name

        def get_member(self, user):
                try:
                        return GroupMember.objects.get( group = self,
                                                         user = user, )
                except GroupMember.DoesNotExist:
                        return None

	def __str__(self):
		return self.name;

	class Admin:
		pass

class GroupMember(models.Model):
        group = models.ForeignKey( Group, edit_inline = models.TABULAR, core = True )
        user = models.ForeignKey( User, core = True, )

        class Admin:
		list_display = ('group', 'user',)
		list_filter = ('group',)

class Theme(models.Model):
	name = models.CharField(maxlength = 250)
	path = models.CharField(maxlength = 250)

	def __str__(self):
		return self.name;

	class Admin:
		pass

NAVIGATION_URL_TYPES = (
        (0, 'Relative (e.g. /wiki/show/Start)'),
        (1, 'Absolute (e.g. http://sphene.net')
        )

NAVIGATION_TYPES = (
        (0, 'Left Main Navigation'),
        (1, 'Top navigation')
        )

class Navigation(models.Model):
        group = models.ForeignKey(Group)
        label = models.CharField(maxlength = 250)
        href  = models.CharField(maxlength = 250)
        urltype = models.IntegerField( default = 0, choices = NAVIGATION_URL_TYPES )
        sortorder = models.IntegerField( default = 100 )
        navigationType = models.IntegerField( default = 0, choices = NAVIGATION_TYPES )


        def __str__(self):
                return self.label

        class Meta:
                ordering = ['sortorder']

        class Admin:
                list_display = ( 'label', 'group', 'href', 'navigationType' )
                list_filter = ( 'group', 'navigationType' )
                ordering = ['group', 'navigationType', 'sortorder']
        

class ApplicationChangelog(models.Model):
        app_label = models.CharField(maxlength = 250)
        model = models.CharField(maxlength = 250)
        version = models.CharField(maxlength = 250)
        applied = models.DateTimeField()

        class Meta:
                get_latest_by = 'applied'


