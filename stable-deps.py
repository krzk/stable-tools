#!/usr/bin/env python
#
# git-deps - automatically detect dependencies between git commits
# Copyright (C) 2013 Adam Spiers <git@adamspiers.org>
#
# The software in this repository is free software: you can redistribute
# it and/or modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation, either version 2 of the
# License, or (at your option) any later version.
#
# This software is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import print_function

import argparse
import json
import logging
import os
import re
import sys
import subprocess
import types
from textwrap import dedent, wrap


def abort(msg, exitcode=1):
    print(msg, file=sys.stderr)
    sys.exit(exitcode)

try:
    import pygit2
except ImportError:
    msg = "pygit2 not installed; aborting."
    install_guide = None
    import platform
    if platform.system() == 'Linux':
        distro, version, d_id = platform.linux_distribution()
        distro = distro.strip()  # why are there trailing spaces??
        if distro == 'openSUSE':
            install_guide = \
                "You should be able to install it with something like:\n\n" \
                "  sudo zypper install python-pygit2"

    if install_guide is None:
        msg += "\n\nIf you figure out a way to install it on your platform,\n" \
               "please submit a new issue with the details at:\n\n" \
               "  https://github.com/aspiers/git-config/issues/new\n\n" \
               "so that it can be documented to help other users."
    else:
        msg += "\n\n" + install_guide
    abort(msg)


class DependencyListener(object):
    """Class for listening to result events generated by
    DependencyDetector.  Add an instance of this class to a
    DependencyDetector instance via DependencyDetector.add_listener().
    """

    def __init__(self, options):
        self.options = options

    def set_detector(self, detector):
        self.detector = detector

    def repo(self):
        return self.detector.repo

    def new_commit(self, commit):
        pass

    def new_dependent(self, dependent):
        pass

    def new_dependency(self, dependent, dependency, path, line_num):
        pass

    def new_path(self, dependent, dependency, path, line_num):
        pass

    def new_line(self, dependent, dependency, path, line_num):
        pass

    def dependent_done(self, dependent, dependencies):
        pass

    def all_done(self):
        pass


class CLIDependencyListener(DependencyListener):
    """Dependency listener for use when running in CLI mode.

    This allows us to output dependencies as they are discovered,
    rather than waiting for all dependencies to be discovered before
    outputting anything; the latter approach can make the user wait
    too long for useful output if recursion is enabled.
    """

    def new_dependency(self, dependent, dependency, path, line_num):
        dependent_sha1 = dependent.hex
        dependency_sha1 = dependency.hex

        if self.options.recurse:
            if self.options.log:
                print("%s depends on:" % dependent_sha1)
            else:
                print("%s %s" % (dependent_sha1, dependency_sha1))
        else:
            if not self.options.log:
                print(dependency_sha1)

        if self.options.log:
            cmd = [
                'git',
                '--no-pager',
                '-c', 'color.ui=always',
                'log', '-n1',
                dependency_sha1
            ]
            print(subprocess.check_output(cmd))
            # dependency = detector.get_commit(dependency_sha1)
            # print(dependency.message + "\n")

        # for path in self.dependencies[dependency]:
        #     print("  %s" % path)
        #     keys = sorted(self.dependencies[dependency][path].keys()
        #     print("    %s" % ", ".join(keys)))


class JSONDependencyListener(DependencyListener):
    """Dependency listener for use when compiling graph data in a JSON
    format which can be consumed by WebCola / d3.  Each new commit has
    to be added to a 'commits' array.
    """

    def __init__(self, options):
        super(JSONDependencyListener, self).__init__(options)

        # Map commit names to indices in the commits array.  This is used
        # to avoid the risk of duplicates in the commits array, which
        # could happen when recursing, since multiple commits could
        # potentially depend on the same commit.
        self._commits = {}

        self._json = {
            'commits': [],
            'dependencies': [],
        }

    def get_commit(self, sha1):
        i = self._commits[sha1]
        return self._json['commits'][i]

    def add_commit(self, commit):
        """Adds the commit to the commits array if it doesn't already exist,
        and returns the commit's index in the array.
        """
        sha1 = commit.hex
        if sha1 in self._commits:
            return self._commits[sha1]
        title, separator, body = commit.message.partition("\n")
        commit = {
            'explored': False,
            'sha1': sha1,
            'name': GitUtils.abbreviate_sha1(sha1),
            'describe': GitUtils.describe(sha1),
            'refs': GitUtils.refs_to(sha1, self.repo()),
            'author_name': commit.author.name,
            'author_mail': commit.author.email,
            'author_time': commit.author.time,
            'author_offset': commit.author.offset,
            'committer_name': commit.committer.name,
            'committer_mail': commit.committer.email,
            'committer_time': commit.committer.time,
            'committer_offset': commit.committer.offset,
            # 'message': commit.message,
            'title': title,
            'separator': separator,
            'body': body.lstrip("\n"),
        }
        self._json['commits'].append(commit)
        self._commits[sha1] = len(self._json['commits']) - 1
        return self._commits[sha1]

    def add_link(self, source, target):
        self._json['dependencies'].append

    def new_commit(self, commit):
        self.add_commit(commit)

    def new_dependency(self, parent, child, path, line_num):
        ph = parent.hex
        ch = child.hex

        new_dep = {
            'parent': ph,
            'child': ch,
        }

        if self.options.log:
            pass  # FIXME

        self._json['dependencies'].append(new_dep)

    def dependent_done(self, dependent, dependencies):
        commit = self.get_commit(dependent.hex)
        commit['explored'] = True

    def json(self):
        return self._json


class GitUtils(object):
    @classmethod
    def abbreviate_sha1(cls, sha1):
        """Uniquely abbreviates the given SHA1."""

        # For now we invoke git-rev-parse(1), but hopefully eventually
        # we will be able to do this via pygit2.
        cmd = ['git', 'rev-parse', '--short', sha1]
        # cls.logger.debug(" ".join(cmd))
        out = subprocess.check_output(cmd).strip()
        # cls.logger.debug(out)
        return out

    @classmethod
    def describe(cls, sha1):
        """Returns a human-readable representation of the given SHA1."""

        # For now we invoke git-describe(1), but eventually we will be
        # able to do this via pygit2, since libgit2 already provides
        # an API for this:
        #   https://github.com/libgit2/pygit2/pull/459#issuecomment-68866929
        #   https://github.com/libgit2/libgit2/pull/2592
        cmd = [
            'git', 'describe',
            '--all',       # look for tags and branches
            '--long',      # remotes/github/master-0-g2b6d591
            # '--contains',
            # '--abbrev',
            sha1
        ]
        # cls.logger.debug(" ".join(cmd))
        out = None
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            if e.output.find('No tags can describe') != -1:
                return ''
            raise

        out = out.strip()
        out = re.sub(r'^(heads|tags|remotes)/', '', out)
        # We already have the abbreviated SHA1 from abbreviate_sha1()
        out = re.sub(r'-g[0-9a-f]{7,}$', '', out)
        # cls.logger.debug(out)
        return out

    @classmethod
    def refs_to(cls, sha1, repo):
        """Returns all refs pointing to the given SHA1."""
        matching = []
        for refname in repo.listall_references():
            symref = repo.lookup_reference(refname)
            dref = symref.resolve()
            oid = dref.target
            commit = repo.get(oid)
            if commit.hex == sha1:
                matching.append(symref.shorthand)

        return matching

class InvalidCommitish(StandardError):
    def __init__(self, commitish):
        self.commitish = commitish

    def message(self):
        return "Couldn't resolve commitish %s" % self.commitish


class DependencyDetector(object):
    """Class for automatically detecting dependencies between git commits.
    A dependency is inferred by diffing the commit with each of its
    parents, and for each resulting hunk, performing a blame to see
    which commit was responsible for introducing the lines to which
    the hunk was applied.

    Dependencies can be traversed recursively, building a dependency
    tree represented (conceptually) by a list of edges.
    """

    def __init__(self, options, repo_path=None, logger=None):
        self.options = options

        if logger is None:
            self.logger = self.default_logger()

        if repo_path is None:
            try:
                repo_path = pygit2.discover_repository('.')
            except KeyError:
                abort("Couldn't find a repository in the current directory.")

        self.repo = pygit2.Repository(repo_path)

        # Nested dict mapping dependents -> dependencies -> files
        # causing that dependency -> numbers of lines within that file
        # causing that dependency.  The first two levels form edges in
        # the dependency graph, and the latter two tell us what caused
        # those edges.
        self.dependencies = {}

        # A TODO list (queue) and dict of dependencies which haven't
        # yet been recursively followed.  Only useful when recursing.
        self.todo = []
        self.todo_d = {}

        # An ordered list and dict of commits whose dependencies we
        # have already detected.
        self.done = []
        self.done_d = {}

        # A cache mapping SHA1s to commit objects
        self.commits = {}

        # Memoization for branch_contains()
        self.branch_contains_cache = {}

        # Callbacks to be invoked when a new dependency has been
        # discovered.
        self.listeners = []

    def add_listener(self, listener):
        if not isinstance(listener, DependencyListener):
            raise RuntimeError("Listener must be a DependencyListener")
        self.listeners.append(listener)
        listener.set_detector(self)

    def notify_listeners(self, event, *args):
        for listener in self.listeners:
            fn = getattr(listener, event)
            fn(*args)

    def default_logger(self):
        if not self.options.debug:
            return logging.getLogger(self.__class__.__name__)

        log_format = '%(asctime)-15s %(levelname)-6s %(message)s'
        date_format = '%b %d %H:%M:%S'
        formatter = logging.Formatter(fmt=log_format, datefmt=date_format)
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(formatter)
        # logger = logging.getLogger(__name__)
        logger = logging.getLogger(self.__class__.__name__)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        return logger

    def get_commit(self, rev):
        if rev in self.commits:
            return self.commits[rev]

        try:
            self.commits[rev] = self.repo.revparse_single(rev)
        except (KeyError, ValueError):
            raise InvalidCommitish(rev)

        return self.commits[rev]

    def find_dependencies(self, dependent_rev, recurse=None):
        """Find all dependencies of the given revision, recursively traversing
        the dependency tree if requested.
        """
        if recurse is None:
            recurse = self.options.recurse

        try:
            dependent = self.get_commit(dependent_rev)
        except InvalidCommitish as e:
            abort(e.message())

        self.todo.append(dependent)
        self.todo_d[dependent.hex] = True

        while self.todo:
            sha1s = [commit.hex[:8] for commit in self.todo]
            self.logger.debug("TODO list: %s" % " ".join(sha1s))
            dependent = self.todo.pop(0)
            del self.todo_d[dependent.hex]
            self.logger.debug("Processing %s from TODO list" %
                              dependent.hex[:8])
            self.notify_listeners('new_commit', dependent)

            for parent in dependent.parents:
                self.find_dependencies_with_parent(dependent, parent)
            self.done.append(dependent.hex)
            self.done_d[dependent.hex] = True
            self.logger.debug("Found all dependencies for %s" %
                              dependent.hex[:8])
            # A commit won't have any dependencies if it only added new files
            dependencies = self.dependencies.get(dependent.hex, {})
            self.notify_listeners('dependent_done', dependent, dependencies)

        self.notify_listeners('all_done')

    def find_dependencies_with_parent(self, dependent, parent):
        """Find all dependencies of the given revision caused by the given
        parent commit.  This will be called multiple times for merge
        commits which have multiple parents.
        """
        self.logger.debug("  Finding dependencies of %s via parent %s" %
                          (dependent.hex[:8], parent.hex[:8]))
        diff = self.repo.diff(parent, dependent,
                              context_lines=self.options.context_lines)
        for patch in diff:
            path = patch.delta.old_file.path
            self.logger.debug("    Examining hunks in %s" % path)
            for hunk in patch.hunks:
                self.blame_hunk(dependent, parent, path, hunk)

    def blame_hunk(self, dependent, parent, path, hunk):
        """Run git blame on the parts of the hunk which exist in the older
        commit in the diff.  The commits generated by git blame are
        the commits which the newer commit in the diff depends on,
        because without the lines from those commits, the hunk would
        not apply correctly.
        """
        first_line_num = hunk.old_start
        line_range_before = "-%d,%d" % (hunk.old_start, hunk.old_lines)
        line_range_after  = "+%d,%d" % (hunk.new_start, hunk.new_lines)
        self.logger.debug("      Blaming hunk %s @ %s" %
                          (line_range_before, parent.hex[:8]))

        if not self.tree_lookup(path, parent):
            # This is probably because dependent added a new directory
            # which was not previously in the parent.
            return

        cmd = [
            'git', 'blame',
            '--porcelain',
            '-L', "%d,+%d" % (hunk.old_start, hunk.old_lines),
            parent.hex, '--', path
        ]
        blame = subprocess.check_output(cmd)

        dependent_sha1 = dependent.hex
        if dependent_sha1 not in self.dependencies:
            self.logger.debug('        New dependent: %s (%s)' %
                              (dependent_sha1[:8], self.oneline(dependent)))
            self.dependencies[dependent_sha1] = {}
            self.notify_listeners('new_dependent', dependent)

        line_to_culprit = {}

        for line in blame.split('\n'):
            # self.logger.debug('      !' + line.rstrip())
            m = re.match('^([0-9a-f]{40}) (\d+) (\d+)( \d+)?$', line)
            if not m:
                continue
            dependency_sha1, orig_line_num, line_num = m.group(1, 2, 3)
            line_num = int(line_num)
            dependency = self.get_commit(dependency_sha1)
            line_to_culprit[line_num] = dependency.hex

            if self.is_excluded(dependency):
                self.logger.debug(
                    '        Excluding dependency %s from line %s (%s)' %
                    (dependency_sha1[:8], line_num,
                     self.oneline(dependency)))
                continue

            if dependency_sha1 not in self.dependencies[dependent_sha1]:
                if dependency_sha1 in self.todo_d:
                    self.logger.debug(
                        '        Dependency %s via line %s already in TODO' %
                        (dependency_sha1[:8], line_num,))
                    continue

                if dependency_sha1 in self.done_d:
                    self.logger.debug(
                        '        Dependency %s via line %s already done' %
                        (dependency_sha1[:8], line_num,))
                    continue

                self.logger.debug(
                    '        New dependency %s via line %s (%s)' %
                    (dependency_sha1[:8], line_num, self.oneline(dependency)))
                self.dependencies[dependent_sha1][dependency_sha1] = {}
                self.notify_listeners('new_commit', dependency)
                self.notify_listeners('new_dependency',
                                      dependent, dependency, path, line_num)
                if dependency_sha1 not in self.dependencies:
                    if self.options.recurse:
                        self.todo.append(dependency)
                        self.todo_d[dependency.hex] = True
                        self.logger.debug('          added to TODO')

            dep_sources = self.dependencies[dependent_sha1][dependency_sha1]

            if path not in dep_sources:
                dep_sources[path] = {}
                self.notify_listeners('new_path',
                                      dependent, dependency, path, line_num)

            if line_num in dep_sources[path]:
                abort("line %d already found when blaming %s:%s" %
                      (line_num, parent.hex[:8], path))

            dep_sources[path][line_num] = True
            self.notify_listeners('new_line',
                                  dependent, dependency, path, line_num)

        diff_format = '      |%8.8s %5s %s%s'
        hunk_header = '@@ %s %s @@' % (line_range_before, line_range_after)
        self.logger.debug(diff_format % ('--------', '-----', '', hunk_header))
        line_num = hunk.old_start
        for line in hunk.lines:
            if line.old_lineno == -1:
                rev = ln = ''
            else:
                rev = line_to_culprit[line_num]
                ln = line_num
                line_num += 1
        #    self.logger.debug(diff_format % (rev, ln, mode, line.rstrip()))

    def oneline(self, commit):
        return commit.message.split('\n', 1)[0]

    def is_excluded(self, commit):
        if self.options.exclude_commits is not None:
            for exclude in self.options.exclude_commits:
                if self.branch_contains(commit, exclude):
                    return True
        return False

    def branch_contains(self, commit, branch):
        sha1 = commit.hex
        branch_commit = self.get_commit(branch)
        branch_sha1 = branch_commit.hex
        self.logger.debug("        Does %s (%s) contain %s?" %
                          (branch, branch_sha1[:8], sha1[:8]))

        if sha1 not in self.branch_contains_cache:
            self.branch_contains_cache[sha1] = {}
        if branch_sha1 in self.branch_contains_cache[sha1]:
            memoized = self.branch_contains_cache[sha1][branch_sha1]
            self.logger.debug("          %s (memoized)" % memoized)
            return memoized

        cmd = ['git', 'merge-base', sha1, branch_sha1]
        # self.logger.debug(" ".join(cmd))
        out = subprocess.check_output(cmd).strip()
        self.logger.debug("        merge-base returned: %s" % out[:8])
        result = out == sha1
        self.logger.debug("          %s" % result)
        self.branch_contains_cache[sha1][branch_sha1] = result
        return result

    def tree_lookup(self, target_path, commit):
        """Navigate to the tree or blob object pointed to by the given target
        path for the given commit.  This is necessary because each git
        tree only contains entries for the directory it refers to, not
        recursively for all subdirectories.
        """
        segments = target_path.split("/")
        tree_or_blob = commit.tree
        path = ''
        while segments:
            dirent = segments.pop(0)
            if isinstance(tree_or_blob, pygit2.Tree):
                if dirent in tree_or_blob:
                    tree_or_blob = self.repo[tree_or_blob[dirent].oid]
                    # self.logger.debug('%s in %s' % (dirent, path))
                    if path:
                        path += '/'
                    path += dirent
                else:
                    # This is probably because we were called on a
                    # commit whose parent added a new directory.
                    self.logger.debug('      %s not in %s in %s' %
                                      (dirent, path, commit.hex[:8]))
                    return None
            else:
                self.logger.debug('      %s not a tree in %s' %
                                  (tree_or_blob, commit.hex[:8]))
                return None
        return tree_or_blob

    def edges(self):
        return [
            [(dependent, dependency)
             for dependency in self.dependencies[dependent]]
            for dependent in self.dependencies.keys()
        ]


def parse_args():
    parser = argparse.ArgumentParser(
        description='Auto-detects commits on which the given '
                    'commit(s) depend.',
        usage='%(prog)s [options] COMMIT-ISH [COMMIT-ISH...]',
        add_help=False
    )
    parser.add_argument('-h', '--help', action='help',
                        help='Show this help message and exit')
    parser.add_argument('-l', '--log', dest='log', action='store_true',
                        help='Show commit logs for calculated dependencies')
    parser.add_argument('-j', '--json', dest='json', action='store_true',
                        help='Output dependencies as JSON')
    parser.add_argument('-s', '--serve', dest='serve', action='store_true',
                        help='Run a web server for visualizing the '
                        'dependency graph')
    parser.add_argument('-b', '--bind-ip', dest='bindaddr', type=str,
                        metavar='IP', default='127.0.0.1',
                        help='IP address for webserver to bind to [%(default)s]')
    parser.add_argument('-p', '--port', dest='port', type=int, metavar='PORT',
                        default=5000,
                        help='Port number for webserver [%(default)s]')
    parser.add_argument('-r', '--recurse', dest='recurse', action='store_true',
                        help='Follow dependencies recursively')
    parser.add_argument('-e', '--exclude-commits', dest='exclude_commits',
                        action='append', metavar='COMMITISH',
                        help='Exclude commits which are ancestors of the '
                        'given COMMITISH (can be repeated)')
    parser.add_argument('-c', '--context-lines', dest='context_lines',
                        type=int, metavar='NUM', default=1,
                        help='Number of lines of diff context to use '
                        '[%(default)s]')
    parser.add_argument('-d', '--debug', dest='debug', action='store_true',
                        help='Show debugging')

    options, args = parser.parse_known_args()

    if options.serve:
        if options.log:
            parser.error('--log does not make sense in webserver mode.')
        if options.json:
            parser.error('--json does not make sense in webserver mode.')
        if options.recurse:
            parser.error('--recurse does not make sense in webserver mode.')
        if len(args) > 0:
            parser.error('Specifying commit-ishs does not make sense in '
                         'webserver mode.')
    else:
        if len(args) == 0:
            parser.error('You must specify at least one commit-ish.')

    return options, args


def cli(options, args):
    detector = DependencyDetector(options)

    if options.json:
        listener = JSONDependencyListener(options)
    else:
        listener = CLIDependencyListener(options)

    detector.add_listener(listener)

    for dependent_rev in args:
        try:
            detector.find_dependencies(dependent_rev)
        except KeyboardInterrupt:
            pass

    if options.json:
        print(json.dumps(listener.json(), sort_keys=True, indent=4))


def serve(options):
    try:
        import flask
        from flask import Flask, send_file, safe_join
        from flask.json import jsonify
    except ImportError:
        abort("Cannot find flask module which is required for webserver mode.")

    webserver = Flask('git-deps')
    here = os.path.dirname(os.path.realpath(__file__))
    root = os.path.join(here, 'html')
    webserver.root_path = root

    ##########################################################
    # Static content

    @webserver.route('/')
    def main_page():
        return send_file('git-deps.html')

    @webserver.route('/tip-template.html')
    def tip_template():
        return send_file('tip-template.html')

    @webserver.route('/test.json')
    def data():
        return send_file('test.json')

    def make_subdir_handler(subdir):
        def subdir_handler(filename):
            path = safe_join(root, subdir)
            path = safe_join(path, filename)
            if os.path.exists(path):
                return send_file(path)
            else:
                flask.abort(404)
        return subdir_handler

    for subdir in ('node_modules', 'css', 'js'):
        fn = make_subdir_handler(subdir)
        route = '/%s/<path:filename>' % subdir
        webserver.add_url_rule(route, subdir + '_handler', fn)

    ##########################################################
    # Dynamic content

    def json_error(status_code, error_class, message, **extra):
        json = {
            'status': status_code,
            'error_class': error_class,
            'message': message,
        }
        json.update(extra)
        response = jsonify(json)
        response.status_code = status_code
        return response

    @webserver.route('/options')
    def send_options():
        client_options = options.__dict__
        client_options['repo_path'] = os.getcwd()
        return jsonify(client_options)

    @webserver.route('/deps.json/<commitish>')
    def deps(commitish):
        detector = DependencyDetector(options)
        listener = JSONDependencyListener(options)
        detector.add_listener(listener)

        try:
            root_commit = detector.get_commit(commitish)
        except InvalidCommitish as e:
            return json_error(
                422, 'Invalid commitish',
                "Could not resolve commitish '%s'" % commitish,
                commitish=commitish)

        detector.find_dependencies(commitish)
        json = listener.json()
        json['root'] = {
            'commitish': commitish,
            'sha1': root_commit.hex,
            'abbrev': GitUtils.abbreviate_sha1(root_commit.hex),
        }
        return jsonify(json)

    # We don't want to see double-decker warnings, so check
    # WERKZEUG_RUN_MAIN which is only set for the first startup, not
    # on app reloads.
    if options.debug and not os.getenv('WERKZEUG_RUN_MAIN'):
        print("!! WARNING!  Debug mode enabled, so webserver is completely "
              "insecure!")
        print("!! Arbitrary code can be executed from browser!")
        print()
    webserver.run(port=options.port, debug=options.debug, host=options.bindaddr)


def main():
    options, args = parse_args()
    # rev_list = sys.stdin.readlines()

    if options.serve:
        serve(options)
    else:
        try:
            cli(options, args)
        except InvalidCommitish as e:
            abort(e.message())


if __name__ == "__main__":
    main()
