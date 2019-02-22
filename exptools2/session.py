import os
import yaml
import os.path as op
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from psychopy import core
from psychopy.iohub import launchHubServer
from psychopy.visual import Window, TextStim
from psychopy.event import waitKeys, Mouse
from psychopy.monitors import Monitor
from psychopy import logging
from psychopy.hardware.emulator import SyncGenerator
from psychopy import prefs as psychopy_prefs

# TODO:
# - merge default settings with user settings (overwrite default)
# - write function that pickles/joblib dump complete exp


class Session:
    """ Base Session class """
    def __init__(self, output_str, settings_file=None, eyetracker_on=False):
        """ Initializes base Session class.

        parameters
        ----------
        output_str : str
            Name (string) for output-files (e.g., 'sub-01_ses-post_run-1')
        settings_file : str
            Path to settings file. If None, default_settings.yml is used
        eyetracker_on : bool
            Whether to enable eyetracker

        attributes
        ----------
        output_dir : str
            Path to output-directory ($PWD/logs)
        settings : dict
            Dictionary with settings from yaml
        clock : psychopy Clock
            Global clock (reset to 0 at start exp)
        timer : psychopy Clock
            Timer used to time phases
        exp_start : float
            Time at actual start of experiment
        log : psychopy Logfile
            Logfile with info about exp (level >= EXP)
        nr_frames : int
            Counter for number of frames for each phase
        win : psychopy Window
            Current window        
        default_fix : TextStim
            Default fixation stim (a TextStim with '+')
        actual_framerate : float
            Estimated framerate of monitor
        """
        self.output_str = output_str
        self.output_dir = op.join(os.getcwd(), 'logs')
        self.settings_file = settings_file
        self.eyetracker_on=eyetracker_on
        self.clock = core.Clock()
        self.timer = core.Clock()
        self.exp_start = None
        self.exp_stop = None
        self.current_trial = None
        self.log = dict(trial_nr=[], onset=[], event_type=[], phase=[], response=[], nr_frames=[])
        self.nr_frames = 0  # keeps track of nr of nr of frames per phase

        # Initialize
        self.settings = self._load_settings()
        self.monitor = self._create_monitor()
        self.win = self._create_window()
        self.mouse = Mouse(**self.settings['mouse'])
        self.logfile = self._create_logfile()
        self.default_fix = TextStim(self.win, '+')
        self.mri_simulator = self._setup_mri_simulator() if self.settings['mri']['simulate'] else None
        self.tracker = None

    def _load_settings(self):
        """ Loads settings and sets preferences. """

        default_settings_path = op.join(op.dirname(__file__), 'data', 'default_settings.yml')
        with open(default_settings_path, 'r') as f_in:
            default_settings = yaml.load(f_in)

        if self.settings_file is None:
            settings = default_settings 
            logging.warn("No settings-file given; using default logfile")
        else:
            if not op.isfile(self.settings_file):
                raise IOError(f"Settings-file ({self.settings_file}) does not exist!")
            
            with open(self.settings_file, 'r') as f_in:
                user_settings = yaml.load(f_in)
            
            settings = default_settings.update(user_settings)

        # Write settings to sub dir
        if not op.isdir(self.output_dir):
            os.makedirs(self.output_dir) 

        settings_out = op.join(self.output_dir, self.output_str + '_expsettings.yml')
        with open(settings_out, 'w') as f_out:
            yaml.dump(settings, f_out, indent=4, default_flow_style=False)

        exp_prefs = settings['preferences']  # set preferences globally
        for preftype, these_settings in exp_prefs.items():
            for key, value in these_settings.items():
                pref_subclass = getattr(psychopy_prefs, preftype)
                pref_subclass[key] = value
                setattr(psychopy_prefs, preftype, pref_subclass)

        return settings

    def _create_monitor(self):
        """ Creates the monitor based on settings and save to disk. """
        monitor = Monitor(**self.settings['monitor'])
        monitor.setSizePix(self.settings['window']['size'])
        monitor.save()  # needed for iohub eyetracker
        return monitor

    def _create_window(self):
        """ Creates a window based on the settings and calculates framerate. """
        win = Window(monitor=self.monitor.name, **self.settings['window'])
        win.flip(clearBuffer=True)
        self.actual_framerate = win.getActualFrameRate()
        t_per_frame = 1. / self.actual_framerate
        logging.warn(f"Actual framerate: {self.actual_framerate:.5f} "
                     f"(1 frame = {t_per_frame:.5f})")
        return win

    def _create_logfile(self):
        """ Creates a logfile. """
        log_path = op.join(self.output_dir, self.output_str + '_log.txt')
        return logging.LogFile(f=log_path, filemode='w', level=logging.EXP) 

    def _setup_mri_simulator(self):
        """ Initializes an MRI simulator (if 'mri' in settings). """
        args = self.settings['mri'].copy()
        args.pop('simulate')
        return SyncGenerator(**args)   

    def start_experiment(self):
        """ Logs the onset of the start of the experiment """
        self.win.callOnFlip(self._set_exp_start)
        self.win.recordFrameIntervals = True
        self.win.flip(clearBuffer=True)  # first frame is synchronized to start exp

    def _set_exp_start(self):
        """ Called upon first win.flip(); timestamps start. """
        self.exp_start = self.clock.getTime()
        self.clock.reset()  # resets global clock
        self.timer.reset()  # phase-timer

        if self.mri_simulator is not None:
            self.mri_simulator.start()

    def _set_exp_stop(self):
        """ Called on last win.flip(); timestamps end. """
        self.exp_stop = self.clock.getTime()

    def display_text(self, text, keys=['return'], **kwargs):
        """ Displays text on the window and waits for a key response.

        parameters
        ----------
        text : str
            Text to display
        keys : str or list[str]
            String (or list of strings) of keyname(s) to wait for
        kwargs : key-word args
            Any (set of) parameter(s) passed to TextStim
        """

        stim = TextStim(self.win, text=text, **kwargs)
        stim.draw()
        self.win.flip()
        waitKeys(keyList=keys)

    def close(self):
        """ 'Closes' experiment. Should always be called, even when
        experiment is quit manually (saves onsets to file). """
        self.win.callOnFlip(self._set_exp_stop)
        self.win.flip(clearBuffer=True)
        dur_last_phase = self.exp_stop - self.log['onset'][-1] 
        self.win.recordFrameIntervals = False

        print(f"Duration experiment: {self.exp_stop:.3f}\n")

        if not op.isdir(self.output_dir):
            os.makedirs(self.output_dir)

        self.log = pd.DataFrame(self.log).set_index('trial_nr')
        self.log['onset_abs'] = self.log['onset'] + self.exp_start

        # Only non-responses have a duration
        self.log['duration'] = np.nan
        nonresp_idx = self.log.event_type != 'response'  # might not cover everything
        durations = np.append(self.log.loc[nonresp_idx, 'onset'].diff().values[1:], dur_last_phase)
        self.log.loc[nonresp_idx, 'duration'] = durations

        # Same for nr frames
        nr_frames = np.append(self.log.loc[nonresp_idx, 'nr_frames'].values[1:], self.nr_frames)
        self.log.loc[nonresp_idx, 'nr_frames'] = nr_frames.astype(int)

        # Save to disk
        f_out = op.join(self.output_dir, self.output_str + '.tsv')
        self.log.to_csv(f_out, sep='\t', index=True)

        fig, ax = plt.subplots(figsize=(15, 5))
        ax.plot(self.win.frameIntervals)
        ax.axhline(1./self.actual_framerate, c='r')
        ax.axhline(1./self.actual_framerate + 1./self.actual_framerate, c='r', ls='--')
        ax.set(xlim=(0, len(self.win.frameIntervals)), xlabel='Frame nr', ylabel='Interval (sec.)')
        fig.savefig(op.join(self.output_dir, self.output_str + '_frames.png'))

        if self.mri_simulator is not None:
            self.mri_simulator.stop()

        core.quit()

    def init_eyetracker(self):
        """ Initializes eyetracker.

        After initialization, tracker object ("device" in iohub lingo)
        can be accessed from self.tracker
        """
        if not self.eyetracker_on:
            raise ValueError("Cannot initialize eyetracker if eyetracker_on=False!")

        EYETRACKER_NAME = 'eyetracker.hw.sr_research.eyelink.EyeTracker'
        # default_native_data_file_name: et_data
        self.iohub = launchHubServer(
            psychopy_monitor_name=self.monitor.name,
            #datastore_name='test_et',
            **{EYETRACKER_NAME: {
                'enable_interface_without_connection': False,
                #'default_native_data_file_name': 'test'
            }}
        )

        self.tracker = self.iohub.devices.eyetracker

    def start_recording_eyetracker(self):
        self.tracker.setRecordingState(True)

    def stop_recording_eyetracker(self):
        self.tracker.setRecordingState(False)

    def calibrate_eyetracker(self):

        if self.tracker is None:
            raise ValueError("Cannot calibrate tracker if it's not initialized yet!")

        self.tracker.runSetupProcedure()

    def close_tracker(self):
        self.stop_recording_eyetracker()
        self.iohub.quit()
