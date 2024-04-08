# Copyright (c) 2017 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.

import os
from datetime import datetime

import nuke
import sgtk
from sgtk.util.filesystem import ensure_folder_exists


HookBaseClass = sgtk.get_hook_baseclass()


class NukeSessionPublishPlugin(HookBaseClass):
    """
    Plugin for publishing an open nuke session.

    This hook relies on functionality found in the base file publisher hook in
    the publish2 app and should inherit from it in the configuration. The hook
    setting for this plugin should look something like this::

        hook: "{self}/publish_file.py:{engine}/tk-multi-publish2/basic/nuke_publish_script.py"

    """

    # NOTE: The plugin icon and name are defined by the base file plugin.

    @property
    def description(self):
        """
        Verbose, multi-line description of what the plugin does. This can
        contain simple html for formatting.
        """

        loader_url = "https://support.shotgunsoftware.com/hc/en-us/articles/219033078"

        return """
        Publishes the file to Shotgun. A <b>Publish</b> entry will be
        created in Shotgun which will include a reference to the file's current
        path on disk. If a publish template is configured, a copy of the
        current session will be copied to the publish template path which
        will be the file that is published. Other users will be able to access
        the published file via the <b><a href='%s'>Loader</a></b> so long as
        they have access to the file's location on disk.

        If the session has not been saved, validation will fail and a button
        will be provided in the logging output to save the file.

        <h3>File versioning</h3>
        If the filename contains a version number, the process will bump the
        file to the next version after publishing.

        The <code>version</code> field of the resulting <b>Publish</b> in
        Shotgun will also reflect the version number identified in the filename.
        The basic worklfow recognizes the following version formats by default:

        <ul>
        <li><code>filename.v###.ext</code></li>
        <li><code>filename_v###.ext</code></li>
        <li><code>filename-v###.ext</code></li>
        </ul>

        After publishing, if a version number is detected in the work file, the
        work file will automatically be saved to the next incremental version
        number. For example, <code>filename.v001.ext</code> will be published
        and copied to <code>filename.v002.ext</code>

        If the next incremental version of the file already exists on disk, the
        validation step will produce a warning, and a button will be provided in
        the logging output which will allow saving the session to the next
        available version number prior to publishing.

        <br><br><i>NOTE: any amount of version number padding is supported. for
        non-template based workflows.</i>

        <h3>Overwriting an existing publish</h3>
        In non-template workflows, a file can be published multiple times,
        however only the most recent publish will be available to other users.
        Warnings will be provided during validation if there are previous
        publishes.
        """ % (
            loader_url,
        )

    @property
    def settings(self):
        """
        Dictionary defining the settings that this plugin expects to receive
        through the settings parameter in the accept, validate, publish and
        finalize methods.

        A dictionary on the following form::

            {
                "Settings Name": {
                    "type": "settings_type",
                    "default": "default_value",
                    "description": "One line description of the setting"
            }

        The type string should be one of the data types that toolkit accepts as
        part of its environment configuration.
        """

        # inherit the settings from the base publish plugin
        base_settings = super(NukeSessionPublishPlugin, self).settings or {}

        # settings specific to this class
        nuke_publish_settings = {
            "Publish Template": {
                "type": "template",
                "default": None,
                "description": "Template path for published work files. Should"
                "correspond to a template defined in "
                "templates.yml.",
            }
        }

        # update the base settings
        base_settings.update(nuke_publish_settings)

        return base_settings

    @property
    def item_filters(self):
        """
        List of item types that this plugin is interested in.

        Only items matching entries in this list will be presented to the
        accept() method. Strings can contain glob patters such as *, for example
        ["maya.*", "file.maya"]
        """
        return ["nuke.session"]

    def accept(self, settings, item):
        """
        Method called by the publisher to determine if an item is of any
        interest to this plugin. Only items matching the filters defined via the
        item_filters property will be presented to this method.

        A publish task will be generated for each item accepted here. Returns a
        dictionary with the following booleans:

            - accepted: Indicates if the plugin is interested in this value at
                all. Required.
            - enabled: If True, the plugin will be enabled in the UI, otherwise
                it will be disabled. Optional, True by default.
            - visible: If True, the plugin will be visible in the UI, otherwise
                it will be hidden. Optional, True by default.
            - checked: If True, the plugin will be checked in the UI, otherwise
                it will be unchecked. Optional, True by default.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process

        :returns: dictionary with boolean keys accepted, required and enabled
        """

        # if a publish template is configured, disable context change. This
        # is a temporary measure until the publisher handles context switching
        # natively.
        if settings.get("Publish Template").value:
            item.context_change_allowed = False

        path = _session_path()

        if not path:
            # the session has not been saved before (no path determined).
            # provide a save button. the session will need to be saved before
            # validation will succeed.
            self.logger.warn(
                "The Nuke script has not been saved.", extra=_get_save_as_action()
            )

        self.logger.info(
            "Nuke '%s' plugin accepted the current Nuke script." % (self.name,)
        )
        return {"accepted": True, "checked": True}
    

    def validate(self, settings, item):
        """
        Validates the given item to check that it is ok to publish. Returns a
        boolean to indicate validity.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process
        :returns: True if item is valid, False otherwise.
        """

        # this method will handle validation specific to the nuke script itself.
        # the base class plugin will handle validation of the file itself

        publisher = self.parent

        path = _session_path()
        # ---- ensure the session has been saved

        if not path:
            # the session still requires saving. provide a save button.
            # validation fails.
            error_msg = "The Nuke script has not been saved."
            self.logger.error(error_msg, extra=_get_save_as_action())
            raise Exception(error_msg)

        # ---- check the session against any attached work template

        # get the path in a normalized state. no trailing separator,
        # separators are appropriate for current os, no double separators,
        # etc.
        path = sgtk.util.ShotgunPath.normalize(path)

        # if the session item has a known work template, see if the path
        # matches. if not, warn the user and provide a way to save the file to
        # a different path
        work_template = item.properties.get("work_template")
        if work_template:
            if not work_template.validate(path):
                self.logger.warning(
                    "The current session does not match the configured work "
                    "template.",
                    extra={
                        "action_button": {
                            "label": "Save File",
                            "tooltip": "Save the current Nuke session to a "
                            "different file name",
                            # will launch wf2 if configured
                            "callback": _get_save_as_action(),
                        }
                    },
                )
            else:
                self.logger.debug("Work template configured and matches session file.")
        else:
            self.logger.debug("No work template configured.")

        # ---- see if the version can be bumped post-publish

        # check to see if the next version of the work file already exists on
        # disk. if so, warn the user and provide the ability to save to the next
        # available version now
        (next_version_path, version) = self._get_next_version_info(path, item)
        
        if next_version_path and os.path.exists(next_version_path):

            # determine the next available version_number. just keep asking for
            # the next one until we get one that doesn't exist.
            while os.path.exists(next_version_path):
                (next_version_path, version) = self._get_next_version_info(
                    next_version_path, item
                )

            error_msg = "The next version of this file already exists on disk."
            self.logger.error(
                error_msg,
                extra={
                    "action_button": {
                        "label": "Save to v%s" % (version,),
                        "tooltip": "Save to the next available version number, "
                        "v%s" % (version,),
                        "callback": lambda: _save_session(next_version_path),
                    }
                },
            )
            raise Exception(error_msg)

        # ---- populate the necessary properties and call base class validation

        # populate the publish template on the item if found
        publish_template_setting = settings.get("Publish Template")
        publish_template = publisher.engine.get_template_by_name(
            publish_template_setting.value
        )
        if publish_template:
            item.properties["publish_template"] = publish_template

        # set the session path on the item for use by the base plugin validation
        # step. NOTE: this path could change prior to the publish phase.
        item.properties["path"] = path

        # run the base class validation
        return super(NukeSessionPublishPlugin, self).validate(settings, item)

    def publish(self, settings, item):
        """
        Executes the publish logic for the given item and settings.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process
        """

        # get the path in a normalized state. no trailing separator, separators        
        # are appropriate for current os, no double separators, etc.
        path = sgtk.util.ShotgunPath.normalize(_session_path())

        # ensure the session is saved
        _save_session(path)

        # search the 'WW_LOCATION' env
        if os.getenv("WW_LOCATION") == 'vietnam':

            org_node = []
            for node in nuke.allNodes():
                if node.Class() in ['Read', 'Write' ]:
                    org_node.append( [node, node['file'].value()] )
                    if 'show' in node['file'].value():
                        partition = node['file'].value().partition('show')
                        new_path = '/show' + partition[2]
                        new_path = new_path.replace( '\\', '/' )
                        
                        node['file'].setValue( new_path )
            nuke.scriptSave()
        
            # ftputil module path append
            import sys

            current_path = os.path.abspath(__file__)

            for i in range(3):
                current_path = os.path.dirname(current_path)
            
            ftp_action_path = os.path.join(current_path, 'ftp_action')

            sys.path.append(ftp_action_path)

            from ftputil import ftputil
            import host

            _host = None
            source_path = ''
            target_path = ''

            # hosting ftp server
            if os.getenv("WW_LOCATION") == 'vietnam':
                ftp_ip = "220.127.148.3"
            else:
                ftp_ip = '10.0.20.38'

            _host = host.ftpHost(
                ftp_ip,
                "west_rnd",
                "rnd2022!"
            )
            # if os.getenv('TK_DEBUG') or os.getenv('USER') == 'w10296':
            #     print("----------------------DEBUG-------------------------")
            #     _host = host.ftpHost(
            #         "10.0.20.38",
            #         "west_rnd",
            #         "rnd2022!"
            #     )
            # else:
            #     _host = host.ftpHost(
            #         "220.127.148.3",
            #         "west_rnd",
            #         "rnd2022!"
            #     )

            # ftp upload action and logging
            if not sys.platform in ["linux2", "linux"]:
                source_path = path.replace("\\", "/")

                target_path = source_path.replace("C:", "")
                target_path = target_path.replace("show", "shotgrid_pub/show")
                target_path = target_path.replace("dev","pub")
                
            else:
                source_path = path

                target_path = source_path.replace("show", "shotgrid_pub/show")
                target_path = target_path.replace("dev","pub")
                
            log_data = list()
            log_data.append("=================================================")
            log_data.append(datetime.today().strftime("%Y/%m/%d %H:%M:%S\n"))

            try:
                print(source_path, "->", target_path)
                _host._upload(source_path,target_path)

            except ftputil.ftp_error.FTPIOError as e:
                print("---------------Create directory--------------")
                target_dir = os.path.dirname(target_path)
                print("path : %s", target_dir)
                _host.makedirs(target_dir)

                log_data.append('Create directory to save nuke file')

                print(source_path, "->", target_path)
                _host._upload(source_path, target_path)
            
            log_data.append('{0} to {1} upload file.'.format(source_path, target_path))
            log_data.append("=================================================")
            _host._ftp_log(log_data)
            
            _host.close()
            print('---------------Ftp server close---------------')
        # update the item with the saved session path
        item.properties["path"] = path

        # add dependencies for the base class to register when publishing
        item.properties[
            "publish_dependencies"
        ] = _nuke_find_additional_script_dependencies()

        # let the base class register the publish

        super(NukeSessionPublishPlugin, self).publish(settings, item)

#        for node, org_path in  org_node:
#            node['file'].setValue( org_path )

        nuke.scriptSave()

        # update 'tag' field
        if os.getenv("WW_LOCATION") == 'vietnam': 
            self.update_last_publishfile_tag(item)

    def finalize(self, settings, item):
        """
        Execute the finalization pass. This pass executes once all the publish
        tasks have completed, and can for example be used to version up files.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process
        """

        # do the base class finalization
        super(NukeSessionPublishPlugin, self).finalize(settings, item)

        # bump the session file to the next version
        if os.getenv('WW_LOCATION') != 'vietnam':
            self._save_to_next_version(item.properties["path"], item, _save_session)
    
    def update_last_publishfile_tag(self, item):
        current_context = self.parent.context
        
        sg_filters = [
            ["project", "is", current_context.project],
            ["entity", "is", current_context.entity],
            ["task", "is", current_context.task],
            ["created_by", "is", current_context.user]
        ]

        sg_fields = ['id', 'created_at', 'tags', 'path']

        find_published_files = self.parent.shotgun.find("PublishedFile", sg_filters, sg_fields, 
                                                        order=[{'field_name':'created_at','direction':'desc'}])

        if find_published_files:
            last_published_file = find_published_files[0]

            last_publish_file_ext = last_published_file['path']['local_path']
            last_publish_file_ext = os.path.splitext(last_publish_file_ext)[1]

            tag_info = self.parent.shotgun.find_one("Tag", [["name", "is", "ww_vietnam"]])
            if not tag_info:
                tag_info = self.parent.shotgun.create("Tag", {"name", "ww_vietnam"})

            if tag_info and last_publish_file_ext == '.nk':
                self.parent.shotgun.update("PublishedFile", last_published_file['id'], {"tags": [tag_info]})
            elif last_publish_file_ext != '.nk':
                print("last publish file is not '.nk'.")


def _nuke_find_additional_script_dependencies():
    """
    Find all dependencies for the current nuke script
    """

    # figure out all the inputs to the scene and pass them as dependency
    # candidates
    dependency_paths = []
    for read_node in nuke.allNodes("Read"):
        # make sure we have a file path and normalize it
        # file knobs set to "" in Python will evaluate to None. This is
        # different than if you set file to an empty string in the UI, which
        # will evaluate to ""!
        file_path = read_node.knob("file").evaluate()
        if not file_path:
            continue
        file_path = sgtk.util.ShotgunPath.normalize(file_path)
        dependency_paths.append(file_path)

    return dependency_paths


def _save_session(path):
    """
    Save the current session to the supplied path.
    """
    # Nuke won't ensure that the folder is created when saving, so we must make sure it exists
    ensure_folder_exists(os.path.dirname(path))
    nuke.scriptSaveAs(path, True)


def _session_path():
    """
    Return the path to the current session
    :return:
    """
    root_name = nuke.root().name()

    return None if root_name == "Root" else root_name


def _get_save_as_action():
    """
    Simple helper for returning a log action dict for saving the session
    """

    engine = sgtk.platform.current_engine()

    # default save callback
    callback = nuke.scriptSaveAs

    # if workfiles2 is configured, use that for file save
    if "tk-multi-workfiles2" in engine.apps:
        app = engine.apps["tk-multi-workfiles2"]
        if hasattr(app, "show_file_save_dlg"):
            callback = app.show_file_save_dlg

    return {
        "action_button": {
            "label": "Save As...",
            "tooltip": "Save the current session",
            "callback": callback,
        }
    }
