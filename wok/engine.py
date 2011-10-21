#!/usr/bin/python2
import os
import sys
import shutil
from datetime import datetime
from optparse import OptionParser, OptionGroup
import logging

import yaml

import wok
from wok import page
from wok import renderers
from wok import util
from wok import devserver

class Engine(object):
    """
    The main engine of wok. Upon initialization, it generates a site from the
    source files.
    """
    default_options = {
        'content_dir' : 'content',
        'template_dir': 'templates',
        'output_dir'  : 'output',
        'media_dir'   : 'media',
        'site_title'  : 'Some random Wok site',
        'url_pattern' : '/{category}/{slug}{page}.{type}',
    }

    def __init__(self, output_lvl = 1):
        """
        Set up CLI options, logging levels, and start everything off.
        Afterwards, run a dev server if asked to.
        """

        # CLI options
        # -----------
        parser = OptionParser(version='%prog v{0}'.format(wok.version))

        # Add option to to run the development server after generating pages
        devserver_grp = OptionGroup(parser, "Development server",
                "Runs a small development server after site generation. \
                --address and --port will be ignored if --server is absent.")
        devserver_grp.add_option('--server', action='store_true',
                dest='runserver',
                help="run a development server after generating the site")
        devserver_grp.add_option('--address', action='store', dest='address',
                help="specify ADDRESS on which to run development server")
        devserver_grp.add_option('--port', action='store', dest='port',
                type='int', 
                help="specify PORT on which to run development server")
        parser.add_option_group(devserver_grp)

        # Options for noisiness level and logging
        logging_grp = OptionGroup(parser, "Logging",
                "By default, log messages will be sent to standard out, \
                and report only errors and warnings.")
        parser.set_defaults(loglevel=logging.WARNING)
        logging_grp.add_option('-q', '--quiet', action='store_const',
                const=logging.ERROR, dest='loglevel',
                help="be completely quiet, log nothing")
        logging_grp.add_option('--warnings', action='store_const',
                const=logging.WARNING, dest='loglevel',
                help="log warnings in addition to errors")
        logging_grp.add_option('-v', '--verbose', action='store_const',
                const=logging.INFO, dest='loglevel',
                help="log ALL the things!")
        logging_grp.add_option('--debug', action='store_const',
                const=logging.DEBUG, dest='loglevel',
                help="log debugging info in addition to warnings and errors")
        logging_grp.add_option('--log', '-l', dest='logfile',
                help="log to the specified LOGFILE instead of standard out")
        parser.add_option_group(logging_grp)

        cli_options, args = parser.parse_args()

        # Set up logging
        # --------------
        logging_options = {
            'format': '%(levelname)s: %(message)s',
            'level': cli_options.loglevel,
        }
        if cli_options.logfile:
            logging_options['filename'] = cli_options.logfile
        else:
            logging_options['stream'] = sys.stdout

        logging.basicConfig(**logging_options)

        # Action!
        # -------
        self.all_pages = []

        self.read_options()
        self.sanity_check()
        self.load_hooks()
        self.prepare_output()
        self.load_pages()
        self.make_tree()
        self.render_site()

        # Dev server
        # ----------
        # Run the dev server after generating pages if the user said to
        if cli_options.runserver:
            devserver.run(cli_options.address, cli_options.port,
                    serv_dir=os.path.join(self.options['output_dir']))

    def read_options(self):
        """Load options from the config file."""
        self.options = Engine.default_options.copy()

        if os.path.isfile('config'):
            with open('config') as f:
                yaml_config = yaml.load(f)

            if yaml_config:
                self.options.update(yaml_config)

        authors = self.options.get('authors', self.options.get('author', None))
        if isinstance(authors, list):
            self.options['authors'] = [page.Author.parse(a) for a in authors]
        elif isinstance(authors, str):
            self.options['authors'] = [page.Author.parse(a) for a in authors.split(',')]

    def sanity_check(self):
        """Basic sanity checks."""
        # Make sure that this is (probabably) a wok source directory.
        if not (os.path.isdir('templates') or os.path.isdir('content')):
            logging.critical("This doesn't look like a wok site. Aborting.")
            sys.exit(1)

    def load_hooks(self):
        sys.path.append('hooks')
        try:
            import __hooks__
            self.hooks = __hooks__.hook
            logging.info('Loaded hooks: {0}'.format(self.hooks))
        except:
            logging.debug('No hooks found')


    def prepare_output(self):
        """
        Prepare the output directory. Remove any contents there already, and
        then copy over the media files, if they exist.
        """
        if os.path.isdir(self.options['output_dir']):
            shutil.rmtree(self.options['output_dir'])
        os.mkdir(self.options['output_dir'])

        # Copy the media directory to the output folder
        try:
            for name in os.listdir(self.options['media_dir']):
                path = os.path.join(self.options['media_dir'], name)
                if os.path.isdir(path):
                    shutil.copytree(
                            path,
                            os.path.join(self.options['output_dir'], name), 
                            symlinks=True
                    )
                else:
                    shutil.copy(path, self.options['output_dir'])

        # Do nothing if the media directory doesn't exist
        except OSError:
            # XXX: We should verify that the problem was the media dir
            pass

    def load_pages(self):
        """Load all the content files."""
        for root, dirs, files in os.walk(self.options['content_dir']):
            # Grab all the parsable files
            for f in files:
                # Don't parse hidden files.
                if f.startswith('.'):
                    continue

                ext = f.split('.')[-1]
                renderer = renderers.Plain

                for r in renderers.all:
                    if ext in r.extensions:
                        renderer = r
                        break
                else:
                    logging.warning('No parser found '
                            'for {0}. Using default renderer.'.format(f))
                    renderer = renderers.Renderer

                p = page.Page(os.path.join(root, f), self.options, renderer)
                if p.meta['published']:
                    self.all_pages.append(p)

    def make_tree(self):
        """
        Make the category pseduo-tree.

        In this structure, each node is a page. Pages with sub pages are
        interior nodes, and leaf nodes have no sub pages. It is not truely a
        tree, because the root node doesn't exist.
        """
        self.categories = {}
        site_tree = []
        # We want to parse these in a approximately breadth first order
        self.all_pages.sort(key=lambda p: len(p.meta['category']))

        for p in [p for p in self.all_pages]:
            if len(p.meta['category']) > 0:
                top_cat = p.meta['category'][0]
                if not top_cat in self.categories:
                    self.categories[top_cat] = []

                self.categories[top_cat].append(p.meta)

            try:
                siblings = site_tree
                for cat in p.meta['category']:
                    # This line will fail if the page is an orphan
                    parent = [subpage for subpage in siblings
                                 if subpage['slug']== cat][0]
                    siblings = parent['subpages']
                siblings.append(p.meta)
            except IndexError:
                logging.error('It looks like the page "{0}" is an orphan! '
                        'This will probably cause problems.'.format(p.path))

    def render_site(self):
        """Render every page and write the output files."""
        # Gather tags
        tag_set = set()
        for p in self.all_pages:
            tag_set = tag_set.union(p.meta['tags'])
        tag_dict = dict()
        for tag in tag_set:
            tag_dict[tag] = [p.meta for p in self.all_pages if tag in p.meta['tags']]


        for p in self.all_pages:
            # Construct this every time, to avoid sharing one instance
            # between page objects.
            templ_vars = {
                'site': {
                    'title': self.options.get('site_title', 'Untitled'),
                    'datetime': datetime.now(),
                    'tags': tag_dict,
                    'pages': self.all_pages[:],
                    'categories': self.categories,
                },
            }

            for k, v in self.options.iteritems():
                if k not in ('site_title', 'output_dir', 'content_dir',
                        'templates_dir', 'media_dir', 'url_pattern'):

                    templ_vars['site'][k] = v

            if 'author' in self.options:
                templ_vars['site']['author'] = self.options['author']

            # Rendering the page might give us back more pages to render.
            new_pages = p.render(templ_vars)
            p.write()
            if new_pages:
                logging.debug('found new_pages')
                self.all_pages += new_pages

if __name__ == '__main__':
    Engine()
