from django.db import models
from django.db.models import Q
from django.contrib.auth.models import User

from sphene.community.models import Group

from django.utils import html
from django.conf import settings
from text import bbcode
bbcode.EMOTICONS_ROOT = settings.MEDIA_URL + 'sphene/emoticons/'
from datetime import datetime

from django.db.models import permalink
from django.core.mail import send_mass_mail
from django.template.context import RequestContext
from django.template import loader, Context
from sphene.community.middleware import get_current_request, get_current_user, get_current_group, get_current_session


import re

"""
Extended Group methods ........
"""

def has_monitor(self):
    return self.__get_monitor(get_current_user())

def has_direct_monitor(self):
    return self.__get_monitor(get_current_user())

def toggle_monitor(self):
    """Toggles monitor and returns the newly created monitor, or None if an
    existing monitor was deleted."""
    if self.has_direct_monitor():
        self.has_direct_monitor().delete()
        if hasattr(self, '__monitor'): delattr(self,'__monitor')
    else:
        monitor = Monitor(user = get_current_user(),
                          group = self, )
        monitor.save()
        self.__monitor = monitor
        return monitor

def __get_monitor(self, user):
    if hasattr(self, '__monitor'): return self.__monitor

    try:
        monitor = Monitor.objects.get( user = user,
                                       group = self,
                                       category__isnull = True,
                                       thread__isnull = True, )
    except Monitor.DoesNotExist:
        monitor = None
    return monitor

Group.has_monitor = has_monitor
Group.has_direct_monitor = has_direct_monitor
Group.toggle_monitor = toggle_monitor
Group.__get_monitor = __get_monitor

"""
END of extended Group methods ...
"""

POSTS_ALLOWED_CHOICES = (
    (-1, 'All Users'),
    (0, 'Loggedin Users'),
    (1, 'Members of the Group'),
    (2, 'Administrators'),
    (3, 'Nobody'),
    )

# Create your models here.
class Category(models.Model):
    name = models.CharField(maxlength = 250)
    group = models.ForeignKey(Group, null = True, blank = True)
    parent = models.ForeignKey('self', related_name = '_childs', null = True, blank = True)
    description = models.TextField(blank = True)
    allowthreads = models.IntegerField( default = 0, choices = POSTS_ALLOWED_CHOICES )
    allowreplies = models.IntegerField( default = 0, choices = POSTS_ALLOWED_CHOICES )

    def do_init(self, initializer, session, user):
        self._initializer = initializer
        self._session = session
        self._user = user
        self.touch( session, user )

    def get_childs(self):
        return self._childs.all().add_initializer(getattr(self, '_initializer', None))

    def canContainPosts(self):
        return self.allowthreads != 3

    def thread_list(self):
        return self.posts.filter( thread__isnull = True )

    def threadCount(self):
        return self.posts.filter( thread__isnull = True ).count()

    def postCount(self):
        return self.posts.count()

    def latestPost(self):
        return self.posts.latest( 'postdate' )

    def allowPostThread(self, user):
        return self.testAllowance(user, self.allowthreads)

    def testAllowance(self, user, level):
        if level == -1:
            return True;
        if user == None or not user.is_authenticated():
            return False;
        if level == 0:
            return True;
        
        return user.has_perm( 'sphboard.add_post' );

    def has_new_posts(self):
        if hasattr(self, 'hasNewPosts'): return self.hasNewPosts
        self.hasNewPosts = self._hasNewPosts(get_current_session(), get_current_user())
        return self.hasNewPosts

    def catchup(self, session, user):
        """Marks all posts in the current thread as read."""
        sKey = self._getSessionKey()
        if sKey in session: del session[sKey]
        return True

    """
      Touches the category object by updating 'lastVisit'
      Returns the datetime object of when it was last visited.
    """
    def touch(self, session, user):
        # Check if we were already "touched" ;)
        if getattr(self, '_touched', False): return self._lastVisit
        self._touched = True
        self.__hasNewPosts = self._hasNewPosts(session, user)
        if not user.is_authenticated(): return None
        try:
            lastVisit = CategoryLastVisit.objects.get( category = self, user = user )
            sKey = self._getSessionKey()
            val = session.get( sKey )
            if not val or not val.has_key( 'originalLastVisit' ):
                val = { 'originalLastVisit': lastVisit.lastvisit,
                        'originalLastVisitSet': datetime.today(), }
                session[sKey] = val
            self._lastVisit = val['originalLastVisit']
        except CategoryLastVisit.DoesNotExist:
            lastVisit = CategoryLastVisit(user = user, category = self)
            self._lastVisit = datetime.today()
        lastVisit.lastvisit = datetime.today()
        lastVisit.save()
        return self._lastVisit

    def hasNewPosts(self):
        req = get_current_request()
        return self._hasNewPosts(req.session, req.user)

    def _hasNewPosts(self, session, user):
        if hasattr(self, '__hasNewPosts'): return self.__hasNewPosts
        if not user.is_authenticated(): return False
        try:
            latestPost = Post.objects.filter( category = self ).latest( 'postdate' )
        except Post.DoesNotExist:
            return False
        
        sKey = self._getSessionKey()
        val = session.get( sKey )
        if not val or not val.has_key('originalLastVisit'):
            # if no original lastvisit is stored, load lastvisit from DB.
            try:
                lastVisit = CategoryLastVisit.objects.get( category = self, user = user )
            except CategoryLastVisit.DoesNotExist:
                return False
            return lastVisit.lastvisit < latestPost.postdate

        if val['originalLastVisit'] > latestPost.postdate:
            return False

        if not val.has_key('thread_lasthits'):
            return True

        lasthits = val['thread_lasthits']

        # Check all posts to see if they are new ....
        allNewPosts = Post.objects.filter( category = self,
                                           postdate__gt = val['originalLastVisit'], )

        for post in allNewPosts:
            threadid = post.thread and post.thread.id or post.id
            if not lasthits.has_key( threadid ) or lasthits[threadid] < post.postdate:
                return True

        # All posts are read .. cool.. we can remove 'thread_lasthits' and adapt 'originalLastVisit'
        del val['thread_lasthits']
        del val['originalLastVisit']
        session[sKey] = val # Store the value back into the session so it gets stored.
        return False

    def toggle_monitor(self):
        """Either creates a monitor if there is none currently, or deletes an
        existing monitor."""
        
        if self.has_direct_monitor():
            self.__get_monitor(get_current_user()).delete()
            if hasattr(self, '__monitor'): delattr(self,'__monitor')
        else:
            monitor = Monitor(group = self.group,
                              user = get_current_user(),
                              category = self)
            monitor.save()
            self.__monitor = monitor
            return monitor

    def has_monitor(self):
        """Returns True if there is a monitor for
        the current user in the current category or any parent category."""
        monitor = self.__get_monitor(get_current_user())
        return monitor

    def has_direct_monitor(self):
        """Only return True if there is a direct monitor for the current
        category."""
        monitor = self.__get_monitor(get_current_user())
        return monitor and monitor.category == self

    def __get_monitor(self, user):
        if hasattr(self, '__monitor'): return self.__monitor
        try:
            monitor = Monitor.objects.get( category = self,
                                           user = user,
                                           thread__isnull = True, )
        except Monitor.DoesNotExist:
            if self.parent:
                monitor = self.parent.has_monitor()
            else:
                monitor = self.group.has_monitor()

        self.__monitor = monitor
        return self.__monitor

    def _getSessionKey(self):
        return 'sphene_sphboard_category_%d' % self.id;

    def __str__(self):
        return self.name;
    
    class Admin:
        search_fields = ('name')

class CategoryLastVisit(models.Model):
    user = models.ForeignKey(User)
    lastvisit = models.DateTimeField()
    category = models.ForeignKey(Category)

    class Admin:
        list_display = ('user', 'lastvisit')
        list_filter = ('user',)
        pass

ALLOWED_TAGS = {
    'p': ( 'align' ),
    'em': (),
    'strike': (),
    'strong': (),
    'img': ( 'src', 'width', 'height', 'border', 'alt', 'title' ),
    'u': ( ),
    }

#USED_STYLE = 'html'
USED_STYLE = 'bbcode'

def htmlentities_replace(test):
    print "entity allowed: %s" % test.group(1)
    return test.group()

def htmltag_replace(test):
    if ALLOWED_TAGS.has_key( test.group(2) ):
        print "tag is allowed.... %s - %s" % (test.group(), test.group(3))
        if test.group(3) == None: return test.group()
        attrs = test.group(3).split(' ')
        allowedParams = ALLOWED_TAGS[test.group(2)]
        i = 1
        allowed = True
        for attr in attrs:
            if attr == '': continue
            val = attr.split('=')
            if not val[0] in allowedParams:
                allowed = False
                print "Not allowed: %s" % val[0]
                break
        if allowed: return test.group()
    print "tag is not allowed ? %s" % test.group(2)
    return test.group().replace('<','&lt;').replace('>','&gt;')

def bbcode_replace(test):
    print "bbcode ... %s %s %s" % (test.group(1), test.group(2), test.group(3))
    return test.group()
POST_STATUS_DEFAULT = 0
POST_STATUS_STICKY = 1
POST_STATUS_CLOSED = 2
POST_STATUS_POLL = 4

POST_STATUSES = {
    'default': 0,
    'sticky': 1,
    'closed': 2,

    'poll': 4,
    }


class Post(models.Model):
    status = models.IntegerField(default = 0, editable = False )
    category = models.ForeignKey(Category, related_name = 'posts', editable = False )
    subject = models.CharField(maxlength = 250)
    body = models.TextField()
    thread = models.ForeignKey('self', null = True, editable = False )
    postdate = models.DateTimeField( auto_now_add = True, editable = False )
    author = models.ForeignKey(User, editable = False )

    def do_init(self, initializer, session, user):
        self._initializer = initializer
        self.hasNewPosts = self._hasNewPosts(session, user)

    def is_sticky(self):
        return self.status & POST_STATUS_STICKY
    def is_closed(self):
        return self.status & POST_STATUS_CLOSED
    def is_poll(self):
        return self.status & POST_STATUS_POLL

    def set_sticky(self, sticky):
        if sticky: self.status = self.status | POST_STATUS_STICKY
        else: self.status = self.status ^ POST_STATUS_STICKY

    def set_closed(self, closed):
        if closed: self.status = self.status | POST_STATUS_CLOSED
        else: self.status = self.status ^ POST_STATUS_CLOSED

    def set_poll(self, poll):
        if poll: self.status = self.status | POST_STATUS_POLL
        else: self.status = self.status ^ POST_STATUS_POLL

    def get_thread(self):
        if self.thread == None: return self;
        return self.thread;

    def latestPost(self):
        return self.allPosts().latest( 'postdate' )

    def allPosts(self):
        return Post.objects.filter( Q( pk = self.id ) | Q( thread = self ) )

    def postCount(self):
        return self.allPosts().count()

    def allowPosting(self, user):
        return self.category.testAllowance( user, self.category.allowreplies )


    def body_escaped(self):
        body = self.body
        if USED_STYLE == 'html':
            regex = re.compile("&(?!nbsp;)");
            body = regex.sub( "&amp;", body )
            regex = re.compile("<(/?)([a-zA-Z]+?)( .*?)?/?>")
            return regex.sub( htmltag_replace, body )
        else:
            """
            body = html.escape( body )
            body = html.linebreaks( body )
            regex = re.compile("\[(.*?)\](?:([^\[]+)\[/(.*?)\])?")
            bbcode = regex.sub( bbcode_replace, body )
            """
            return bbcode.bb2xhtml(body)

    def touch(self, session, user):
        return self._touch( session, user )

    def _touch(self, session, user):
        if not user.is_authenticated(): return None
        if not self._hasNewPosts(session, user): return
        sKey = self.category._getSessionKey()
        val = session.get( sKey )
        if not val: val = { }
        if not val.has_key( 'thread_lasthits' ): val['thread_lasthits'] = { }
        val['thread_lasthits'][self.id] = datetime.today()
        session[sKey] = val

    def _hasNewPosts(self, session, user):
        if not user.is_authenticated(): return False
        try:
            latestPost = Post.objects.filter( thread = self.id ).latest( 'postdate' )
        except Post.DoesNotExist:
            # if no post was found, the thread is the latest post ...
            latestPost = self
        categoryLastVisit = self.category.touch(session, user)
        if categoryLastVisit > latestPost.postdate:
            return False
        sKey = self.category._getSessionKey()
        val = session.get( sKey )
        if not val or not val.has_key( 'thread_lasthits' ) or not val['thread_lasthits'].has_key(self.id):
            return True
        return val['thread_lasthits'][self.id] < latestPost.postdate

    def poll(self):
        try:
            return self.poll_set.all().add_initializer( getattr(self, '_initializer', None) ).get()
        except Poll.DoesNotExist:
            return None

    def has_monitor(self):
        """Returns True if there is a monitor for the current user in this
        thread. (Will also return True if there is a monitor in a category !)
        To check this, call has_direct_monitor !
        """
        monitor = self.__get_monitor(get_current_user())
        if monitor: return True
        return False

    def has_direct_monitor(self):
        """Return only True if there is a direct monitor for THIS thread."""
        monitor = self.__get_monitor(get_current_user())
        thread = self.thread or self
        return monitor and monitor.thread == thread

    def __get_monitor(self, user):
        if hasattr(self, '__monitor'): return self.__monitor
        thread = self.thread or self
        try:
            monitor = Monitor.objects.get( thread = thread,
                                           user = user, )
        except Monitor.DoesNotExist:
            monitor = thread.category.has_monitor()
        self.__monitor = monitor
        return self.__monitor

    def toggle_monitor(self):
        if self.has_direct_monitor():
            self.__get_monitor(get_current_user()).delete()
            if hasattr(self, '__monitor'): delattr(self,'__monitor')
        else:
            thread = self.thread or self
            monitor = Monitor( thread = thread,
                               category = thread.category,
                               group = thread.category.group,
                               user = get_current_user(), )
            monitor.save()
            self.__monitor = monitor
            return monitor

    def save(self):
        isnew = not self.id
        ret = super(Post, self).save()

        if isnew:
            # Email Notifications ....
            thread = self.thread or self
            # thread monitors ..
            myQ = Q( thread = thread )
            # any category monitors
            category = self.category
            while category:
                myQ = myQ | Q( category = category )
                category = category.parent
            # group monitors
            myQ = myQ | Q( group = self.category.group )
            monitors = Monitor.objects.filter(myQ)
            subject = 'New Forum Post: %s' % self.subject
            group = get_current_group() or self.category.group
            t = loader.get_template('sphene/sphboard/new_post_email.txt')
            c = {
                'baseurl': group.baseurl,
                'group': group,
                'post': self,
                }
            body = t.render(RequestContext(get_current_request(), c))
            #body = ("%s just posted in a thread or forum you are monitoring: \n" + \
            #        "Visit http://%s/%s") % (group.baseurl, self.author.get_full_name(), self.get_absolute_url())
            datatuple = ()
            sent_email_addresses = ()
            for monitor in monitors:
                if monitor.user.email in sent_email_addresses: continue
                datatuple += (subject, body, None, (monitor.user.email,)),
                sent_email_addresses += monitor.user.email,

            send_mass_mail(datatuple, )
            print "Sent emails ... %s" % str(datatuple)
        
        return ret

    def __str__(self):
        return self.subject

    def get_absolute_url(self):
        return ('sphene.sphboard.views.showThread', (), { 'groupName': self.category.group.name, 'thread_id': self.thread and self.thread.id or self.id })
    get_absolute_url = permalink(get_absolute_url, get_current_request)

    class Admin:
        pass


class Monitor(models.Model):
    """Monitors allow user to get notified by email on new posts in a
    particular thread, category or a whole board of a group."""
    
    thread = models.ForeignKey(Post, null = True, blank = True)
    category = models.ForeignKey(Category, null = True, blank = True)
    group = models.ForeignKey(Group)
    user = models.ForeignKey(User)

    class Admin:
        list_display = ('user', 'group', 'category', 'thread')
        list_filter = ('user', 'group')


class Poll(models.Model):
    post = models.ForeignKey(Post, editable = False)
    question = models.CharField( maxlength = 250 )
    choices_per_user = models.IntegerField( )

    def do_init(self, initializer, session, user):
        self._initializer = initializer
        self._user = user
        
    def multiplechoice(self):
        return self.choices_per_user != 1

    def choices(self):
        return self.pollchoice_set.all()

    def has_voted(self, user = None):
        if not user: user = self._user
        if not user.is_authenticated(): return False
        return self.pollvoters_set.filter( user = user ).count() > 0
        """try:
            return True
        except PollVoters.DoesNotExist:
            return False
        """

    def total_voters(self):
        from django.db import connection
        cursor = connection.cursor()
        cursor.execute("SELECT COUNT(DISTINCT user_id) as totalvoters FROM sphboard_pollvoters WHERE poll_id = %s", [self.id])
        row = cursor.fetchone()
        return row[0]

    def total_votes(self):
        return self.pollvoters_set.count()

    def null_votes(self):
        return self.pollvoters_set.filter( choice__isnull = True ).count()

    class Admin:
        pass

class PollChoice(models.Model):
    poll = models.ForeignKey(Poll, editable = False)
    choice = models.CharField( maxlength = 250 )
    count = models.IntegerField()

    class Admin:
        pass

class PollVoters(models.Model):
    poll = models.ForeignKey(Poll, editable = False)
    choice = models.ForeignKey(PollChoice, null = True, blank = True, editable = False)
    user = models.ForeignKey(User, editable = False)

