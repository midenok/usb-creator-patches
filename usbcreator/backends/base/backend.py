import usbcreator.install
from usbcreator import misc
import logging
import os
import sys

if sys.version_info[0] < 3:
    import ConfigParser as configparser
else:
    import configparser

def abstract(func):
    def not_implemented(*args):
        raise NotImplementedError('%s is not implemented by the backend.' %
                                  func.__name__)
    return not_implemented

class Config:
    def __init__(self, backend):
        self.file = os.path.expanduser('~/.usb-creator.ini')
        self.folder = os.path.expanduser('~')
        self.parser = configparser.SafeConfigParser()
        self.backend = backend

    def load(self):
        p = self.parser
        p.read(self.file)
        try:
            folder = p.get('global', 'folder')
            self.folder = folder
        except:
            python_govno = 'Yes!'
        source = 1
        s = 'images'
        if p.has_section(s):
            for f in p.items(s):
                self.backend.add_image(f[1])
                if source:
                    self.backend.set_current_source(f[1])
                    source = 0

    def save(self):
        p = self.parser
        s = 'global'
        if p.has_section(s):
            p.remove_section(s)
        p.add_section(s)
        p.set(s, 'folder', self.folder)
        s = 'images'
        if p.has_section(s):
            p.remove_section(s)
        p.add_section(s)
        n = 0
        for image_file in self.backend.sources.keys():
            p.set(s, str(n), image_file)
            n = n + 1
        with open(self.file, 'w') as configfile:
            p.write(configfile)

class Backend:
    def __init__(self):
        self.sources = {}
        self.targets = {}
        self.current_source = None
        self.install_thread = None
        self.config = Config(self)

    # Public methods.

    def add_image(self, filename):
        logging.debug('Backend told to add: %s' % filename)
        filename = os.path.abspath(os.path.expanduser(filename))
        if not os.path.isfile(filename):
            return
        if filename in self.sources:
            logging.warn('Source already added.')
            # TODO evand 2009-07-27: Scroll to source and select.
            return

        extension = os.path.splitext(filename)[1]
        # TODO evand 2009-07-25: What's the equivalent of `file` on Windows?
        # Going by extension is a bit rough.
        if not extension:
            logging.error('File did not have an extension.  '
                          'Could not determine file type.')
            # TODO evand 2009-07-26: Error dialog.
            return

        extension = extension.lower()
        if extension == '.iso':
            label = self._is_casper_cd(filename)
            if label:
                self.sources[filename] = {
                    'device' : filename,
                    'size' : os.path.getsize(filename),
                    'label' : label,
                    'type' : misc.SOURCE_ISO,
                }
                if misc.callable(self.source_added_cb):
                    self.source_added_cb(filename)
        elif extension == '.img':
            self.sources[filename] = {
                'device' : filename,
                'size' : os.path.getsize(filename),
                'label' : '',
                'type' : misc.SOURCE_IMG,
            }
            if misc.callable(self.source_added_cb):
                self.source_added_cb(filename)
        else:
            logging.error('Filename extension type not supported.')

    @abstract
    def detect_devices(self):
        pass

    def set_current_source(self, source):
        if source == None or source in self.sources:
            self.current_source = source
        else:
            raise KeyError(source)
        self.update_free()

    def get_current_source(self):
        return self.current_source

    # Common helpers

    def _device_removed(self, device):
        logging.debug('Device has been removed from the system: %s' % device)
        if device in self.sources:
            if misc.callable(self.source_removed_cb):
                self.source_removed_cb(device)
            self.sources.pop(device)
        elif device in self.targets:
            if misc.callable(self.target_removed_cb):
                self.target_removed_cb(device)
            self.targets.pop(device)

    # Signals.

    def source_added_cb(self, drive):
        pass

    def target_added_cb(self, drive):
        pass

    def source_removed_cb(self, drive):
        pass

    def target_removed_cb(self, drive):
        pass

    def target_changed_cb(self, udi):
        pass

    def format_ended_cb(self):
        pass

    def format_failed_cb(self, message):
        pass

    # Installation signals.

    def success_cb(self):
        pass

    def failure_cb(self, message=None):
        pass

    def install_progress_cb(self, complete, remaining, speed):
        pass

    def install_progress_message_cb(self, message):
        pass

    def install_progress_pulse_cb(self):
        pass

    def install_progress_pulse_stop_cb(self):
        pass

    def retry_cb(self, message):
        pass

    def update_free(self):
        if not self.current_source:
            return True

        for k in self.targets:
            status = self.targets[k]['status']
            if status == misc.NEED_FORMAT or status == misc.CANNOT_USE:
                continue
            changed = self._update_free(k)
            if changed and misc.callable(self.target_changed_cb):
                self.target_changed_cb(k)
        return True

    # Internal functions.

    def _update_free(self, k):
        # TODO evand 2009-08-28: Replace this mess with inotify watches.
        # Incorporate accounting for files we will delete.  Defer updates if
        # sufficient time has not passed since the last update.
        if not self.current_source:
            return False
        current_source = self.sources[self.current_source]
        changed = False
        target = self.targets[k]
        free = target['free']
        target['free'] = misc.fs_size(target['mountpoint'])[1]
        if free != target['free']:
            changed = True

        target = self.targets[k]
        status = target['status']
        target['status'] = misc.CAN_USE
        target['persist'] = 0
        if target['capacity'] < current_source['size']:
            target['status'] = misc.CANNOT_USE
        elif target['free'] < current_source['size']:
            target['status'] = misc.NEED_SPACE
        else:
            target['persist'] = (target['free'] - current_source['size'] -
                                 misc.PADDING * 1024 * 1024)
        if status != target['status']:
            changed = True
        # casper cannot handle files larger than MAX_PERSISTENCE (4GB)
        persist_max = misc.MAX_PERSISTENCE * 1024 * 1024 - 1
        if target['persist'] > persist_max:
            target['persist'] = persist_max
        return changed

    def install(self, source, target, persist, device=None,
                allow_system_internal=False):
        fastboot_mode = self.__class__.__name__ == 'FastbootBackend'
        logging.debug('Starting install thread.')
        self.install_thread = usbcreator.install.install(
            source, target, persist, device=device,
            allow_system_internal=allow_system_internal,
            fastboot_mode=fastboot_mode)
        # Connect signals.
        self.install_thread.success = self.success_cb
        self.install_thread.failure = self.failure_cb
        self.install_thread.progress = self.install_progress_cb
        self.install_thread.progress_message = self.install_progress_message_cb
        self.install_thread.progress_pulse = self.install_progress_pulse_cb
        self.install_thread.progress_pulse_stop = self.install_progress_pulse_stop_cb
        self.install_thread.retry = self.retry_cb
        self.install_thread.start()

    def cancel_install(self):
        logging.debug('cancel_install')
        if self.install_thread and self.install_thread.is_alive():
            # TODO evand 2009-07-24: Should set the timeout for join, and
            # continue to process in a loop until the thread finishes, calling
            # into the frontend for UI event processing then break.  The
            # frontend should notify the user that it's canceling by changing
            # the progress message to "Canceling the installation..." and
            # disabiling the cancel button.
            self.install_thread.join()
