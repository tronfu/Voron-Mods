# Named probe that supports multiple probes
# based on the Klipper probe framework
#
# Copyright (C) 2023  Tron Fu <tron@riverwatcher.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
# Version: 1.0B2

import logging
import pins
import mcu
import sys
sys.path.append("..")
from kinematics import hybrid_corexy
import stepper
import types
from . import manual_probe
from . import probe

HINT_TIMEOUT = """
If the probe did not move far enough to trigger, then
consider reducing the Z axis minimum position so the probe
can travel further (the Z minimum position can be negative).
"""

def unregister_chip(self, chip_name):
    chip_name = chip_name.strip()
    if chip_name in self.chips:
        del self.chips[chip_name]
        del self.pin_resolvers[chip_name]
def unregister_event_handler(self, event, callbacks):
    handlers = self.event_handlers.setdefault(event, [])
    for callback in callbacks:
        if callback in handlers:
            handlers.remove(callback)
def switch_z_virtual_endstop(self, mcu_endstop):
    pin_name = "named_probe:z_virtual_endstop"
    endstop = self.endstop_map.get(pin_name, None)
    if endstop is None:
        return
    # switch the endstop
    self.endstop_map[pin_name]['endstop'] = mcu_endstop
    stepper_name = "z"
    for i, endstop in enumerate(self.endstops):
         if endstop[1]==stepper_name:
            self.endstops[i] = (mcu_endstop, stepper_name)

class PrinterNamedProbe(probe.PrinterProbe):
    def __init__(self, config, mcu_probe):
        self.printer = config.get_printer()
        self.name = config.get_name()
        self.mcu_probe = mcu_probe
        self.speed = config.getfloat('speed', 5.0, above=0.)
        self.lift_speed = config.getfloat('lift_speed', self.speed, above=0.)
        self.x_offset = config.getfloat('x_offset', 0.)
        self.y_offset = config.getfloat('y_offset', 0.)
        self.z_offset = config.getfloat('z_offset')
        self.probe_calibrate_z = 0.
        self.multi_probe_pending = False
        self.last_state = False
        self.last_z_result = 0.
        self.gcode_move = self.printer.load_object(config, "gcode_move")
        # Infer Z position to move to during a probe
        if config.has_section('stepper_z'):
            zconfig = config.getsection('stepper_z')
            self.z_position = zconfig.getfloat('position_min', 0.,
                                               note_valid=False)
        else:
            pconfig = config.getsection('printer')
            self.z_position = pconfig.getfloat('minimum_z_position', 0.,
                                               note_valid=False)
        # Multi-sample support (for improved accuracy)
        self.sample_count = config.getint('samples', 1, minval=1)
        self.sample_retract_dist = config.getfloat('sample_retract_dist', 2.,
                                                   above=0.)
        atypes = {'median': 'median', 'average': 'average'}
        self.samples_result = config.getchoice('samples_result', atypes,
                                               'average')
        self.samples_tolerance = config.getfloat('samples_tolerance', 0.100,
                                                 minval=0.)
        self.samples_retries = config.getint('samples_tolerance_retries', 0,
                                             minval=0)
        self.gcode = self.printer.lookup_object('gcode')                                     
        self.name = config.get_name().split()[-1]
        self.event_handlers = {}
        self.idex_carriage = config.getint('idex_carriage', None)
        self.gcode.register_mux_command("ACTIVATE_PROBE", "PROBE", self.name,
                                   self.cmd_ACTIVATE_PROBE,
                                   desc=self.cmd_ACTIVATE_PROBE_help)
        
        if not callable(getattr(self.printer, "unregister_event_handler", None)):
            self.printer.unregister_event_handler = types.MethodType(unregister_event_handler, self.printer)
        self.event_handlers["homing:homing_move_begin"] = self._handle_homing_move_begin
        self.event_handlers["homing:homing_move_end"] = self._handle_homing_move_end
        self.event_handlers["homing:home_rails_begin"] = self._handle_home_rails_begin
        self.event_handlers["homing:home_rails_end"] = self._handle_home_rails_end
        self.event_handlers["gcode:command_error"] = self._handle_command_error
        
        self.query_probes = self.printer.load_object(config, 'query_named_probes')
        # query_probes will unregister currently active probe
        self.query_probes.register_probe(self)
        
        # Register z_virtual_endstop pin
        self.printer.lookup_object('pins').register_chip("named_probe", self)
        # Register homing event handlers
        self.register_event_handlers()
        # Register PROBE/QUERY_PROBE commands
        self.register_commands()
    def unregister_event_handler(self, event):
        self.printer.unregister_event_handler(event, [self.event_handlers[event]])
    def unregister_commands(self):
        for cmd in ['PROBE', 'QUERY_PROBE', 'PROBE_CALIBRATE', 'PROBE_ACCURACY', 
                'Z_OFFSET_APPLY_PROBE']:
            self.gcode.register_command(cmd, None)
    def unregister_event_handlers(self):
        # Unregister homing event handlers
        for event in self.event_handlers.keys():
            self.unregister_event_handler(event)
    def get_event_handler_by_name(self, name):
        if name in self.event_handlers:
            return self.event_handlers[name]
        else:      # return mcu_probe's event handler as well
            return self.mcu_probe.get_event_handler_by_name(name)
    def register_event_handlers(self):
        # Register homing event handlers
        self.printer.register_event_handler("homing:homing_move_begin",
                                            self._handle_homing_move_begin)
        self.printer.register_event_handler("homing:homing_move_end",
                                            self._handle_homing_move_end)
        self.printer.register_event_handler("homing:home_rails_begin",
                                            self._handle_home_rails_begin)
        self.printer.register_event_handler("homing:home_rails_end",
                                            self._handle_home_rails_end)
        self.printer.register_event_handler("gcode:command_error",
                                            self._handle_command_error)
    def register_commands(self):
        # Register PROBE/QUERY_PROBE commands
        self.gcode.register_command('PROBE', self.cmd_PROBE,
                                    desc=self.cmd_PROBE_help)
        self.gcode.register_command('QUERY_PROBE', self.cmd_QUERY_PROBE,
                                    desc=self.cmd_QUERY_PROBE_help)
        self.gcode.register_command('PROBE_CALIBRATE', self.cmd_PROBE_CALIBRATE,
                                    desc=self.cmd_PROBE_CALIBRATE_help)
        self.gcode.register_command('PROBE_ACCURACY', self.cmd_PROBE_ACCURACY,
                                    desc=self.cmd_PROBE_ACCURACY_help)
        self.gcode.register_command('Z_OFFSET_APPLY_PROBE',
                                    self.cmd_Z_OFFSET_APPLY_PROBE,
                                    desc=self.cmd_Z_OFFSET_APPLY_PROBE_help)
    def activate(self):
        # query_probes will deactivate the current active probe
        self.query_probes.activate_probe(self)
        
        pins = self.printer.lookup_object('pins')
        pins.register_chip('named_probe', self)
        
        # Register homing event handlers
        self.register_event_handlers()
        # Register PROBE/QUERY_PROBE commands
        self.register_commands()
        
        # switch the virtual_endstop on the z rail
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        if len(kin.rails) >= 3:
            z_rail = kin.rails[2]
            if not callable(getattr(z_rail, "switch_z_virtual_endstop", None)):
                z_rail.switch_z_virtual_endstop = types.MethodType(switch_z_virtual_endstop, z_rail)
            z_rail.switch_z_virtual_endstop(self.mcu_probe)
    def deactivate(self):
        # Add method to pins for unregistering chip
        pins = self.printer.lookup_object('pins')
        if not callable(getattr(pins, "unregister_chip", None)):
            pins.unregister_chip = types.MethodType(unregister_chip, pins)
        # Unregister z_virtual_endstop pin
        pins.unregister_chip('named_probe')
        self.unregister_event_handlers()
        self.unregister_commands()
        
    def run_probe(self, gcmd, samples=None, ignore_tolerance=False):
        speed = gcmd.get_float("PROBE_SPEED", self.speed, above=0.)
        lift_speed = self.get_lift_speed(gcmd)
        sample_count = gcmd.get_int("SAMPLES", None, minval=1)
        if sample_count is None:
            if samples != None:
                sample_count = samples
            else:
                sample_count = self.sample_count
        sample_retract_dist = gcmd.get_float("SAMPLE_RETRACT_DIST",
                                             self.sample_retract_dist, above=0.)
        samples_tolerance = gcmd.get_float("SAMPLES_TOLERANCE",
                                           self.samples_tolerance, minval=0.)
        samples_retries = gcmd.get_int("SAMPLES_TOLERANCE_RETRIES",
                                       self.samples_retries, minval=0)
        samples_result = gcmd.get("SAMPLES_RESULT", self.samples_result)
        must_notify_multi_probe = not self.multi_probe_pending
        if must_notify_multi_probe:
            self.multi_probe_begin()
        probexy = self.printer.lookup_object('toolhead').get_position()[:2]
        retries = 0
        positions = []
        while len(positions) < sample_count:
            # Probe position
            pos = self._probe(speed)
            positions.append(pos)
            # Check samples tolerance
            z_positions = [p[2] for p in positions]
            if max(z_positions) - min(z_positions) > samples_tolerance:
                if ignore_tolerance:
                    gcmd.respond_info("Probe samples exceed tolerance. Ignoring...")
                else:
                    if retries >= samples_retries:
                        raise gcmd.error("Probe samples exceed samples_tolerance")
                    gcmd.respond_info("Probe samples exceed tolerance. Retrying...")
                    retries += 1
                    positions = []
            # Retract
            if len(positions) < sample_count:
                self._move(probexy + [pos[2] + sample_retract_dist], lift_speed)
        if must_notify_multi_probe:
            self.multi_probe_end()
        # Calculate and return result
        if samples_result == 'median':
            return self._calc_median(positions)
        return self._calc_mean(positions)
    cmd_ACTIVATE_PROBE_help = "ACTIVATE_PROBE PROBE=<probe name>"
    def cmd_ACTIVATE_PROBE(self, gcmd):
        if self.query_probes.is_probe_active(self):
             gcmd.respond_info("Probe %s is already activated" % (self.name))
             return
        self.activate()
        gcmd.respond_info("Probe %s activated" % (self.name))
    cmd_QUERY_PROBE_help = "Return the status of the z-probe"
    def cmd_QUERY_PROBE(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        print_time = toolhead.get_last_move_time()
        res = self.mcu_probe.query_endstop(print_time)
        self.last_state = res
        gcmd.respond_info("probe %s: %s" % (self.name, ["open", "TRIGGERED"][not not res],))

# Helper code that can probe a series of points and report the
# position at each point.
class NamedProbePointsHelper(probe.ProbePointsHelper):
    def __init__(self, config, finalize_callback, default_points=None):
        self.finalize_callback = finalize_callback
        if callable(getattr(config, "get_printer", None)):
            super().__init__(config)
        else:
            probe_helper = config
            self.printer = probe_helper.printer
            self.probe_points = probe_helper.probe_points
            self.name = probe_helper.name
            self.gcode = probe_helper.gcode
            self.horizontal_move_z = probe_helper.horizontal_move_z
            self.speed = probe_helper.speed
            self.use_offsets = probe_helper.use_offsets
            # Internal probing state
            self.lift_speed = self.speed
            self.probe_offsets = (0., 0., 0.)
            self.results = []
    def start_probe(self, gcmd):
        manual_probe.verify_no_manual_probe(self.printer)
        # Lookup objects
        query_probes = self.printer.lookup_object('query_named_probes')
        probe = query_probes.get_active_probe()
        
        method = gcmd.get('METHOD', 'automatic').lower()
        self.results = []
        if probe is None or method != 'automatic':
            # Manual probe
            self.lift_speed = self.speed
            self.probe_offsets = (0., 0., 0.)
            self._manual_probe_start()
            return
        # Perform automatic probing
        self.lift_speed = probe.get_lift_speed(gcmd)
        self.probe_offsets = probe.get_offsets()
        if self.horizontal_move_z < self.probe_offsets[2]:
            raise gcmd.error("horizontal_move_z can't be less than"
                             " probe's z_offset")
        probe.multi_probe_begin()
        while 1:
            done = self._move_next()
            if done:
                break
            pos = probe.run_probe(gcmd)
            self.results.append(pos)
        probe.multi_probe_end()

def load_config_prefix(config):
    return PrinterNamedProbe(config, probe.ProbeEndstopWrapper(config))
