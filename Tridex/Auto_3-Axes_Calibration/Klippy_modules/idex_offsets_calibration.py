# Support the calibrating the z_offset and x_offset of dual carriage
#
# Copyright (C) 2023  Tron Fu <tron@riverwatcher.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
# Version: 1.0B1

import logging
from mcu import MCU_endstop

ERROR_NO_PROBE = ("No named probe found")

class IDEXOffsetsCalibrationHelper:
    def __init__(self, config):
        self.state = None
        self.positions_endstop = {'x': None, 'y': None, 'z': None, 'dual_carriage': None}
        self.positions_min = {'x': None, 'y': None, 'z': None, 'dual_carriage': None}
        self.positions_max = {'x': None, 'y': None, 'z': None, 'dual_carriage': None}
        self.z_homing = None
        self.last_offsets = {
            'x': None,
            'y': None,
            'z': None
        }
        self.carriage_state = {}
        self.config = config
        self.printer = config.get_printer()
#         self.switch_offset = config.getfloat('switch_offset', 0.0, above=0.)
        self.max_deviation = config.getfloat('max_deviation', 1.0, above=0.)
        self.speed = config.getfloat('speed', 50.0, above=0.)
        self.samples = config.getint('samples', None, minval=1)
        self.tolerance = config.getfloat('samples_tolerance', None, above=0.)
        self.retries = config.getint('samples_tolerance_retries',
                                     None, minval=0)
        atypes = {'none': None, 'median': 'median', 'average': 'average'}
        self.samples_result = config.getchoice('samples_result', atypes,
                                               'none')
        self.lift_speed = config.getfloat('lift_speed', 1000., above=0.)
        self.clearance = config.getfloat('clearance', 20., above=0.)
        self.probing_speed = config.getfloat('probing_speed', None, above=0.)
        self.second_speed = config.getfloat('probing_second_speed',
                                            None, above=0.)
        self.sample_retract_dist = config.getfloat('sample_retract_dist',
                                            None, above=0.)
        self.position_min = config.getfloat('position_min', None)
        self.first_fast = config.getboolean('probing_first_fast', False)
        self.offset_x_probe_site = {
            't0_x': config.getfloat('offset_x_probe_site_t0_x'),
            't1_x': config.getfloat('offset_x_probe_site_t1_x', None),
            'y': config.getfloat('offset_x_probe_site_y'),
            'z': config.getfloat('offset_x_probe_site_z', 0.0)
        }
        if self.offset_x_probe_site['t1_x'] == None:
            self.offset_x_probe_site['t1_x'] = self.offset_x_probe_site['t0_x']
        self.offset_x_step_size = {
            't0': config.getfloat('offset_x_step_size_t0'),
            't1': config.getfloat('offset_x_step_size_t1', None)
        }
        if self.offset_x_step_size['t1'] is None:
            self.offset_x_step_size['t1'] = self.offset_x_step_size['t0']
        self.offset_y_probe_site = {
            't0_x': config.getfloat('offset_y_probe_site_t0_x'),
            't1_x': config.getfloat('offset_y_probe_site_t1_x', None),
            'y': config.getfloat('offset_y_probe_site_y'),
            'z': config.getfloat('offset_y_probe_site_z', 0.0)
        }
        self.offset_y_step_size = {
            't0': config.getfloat('offset_y_step_size_t0'),
            't1': config.getfloat('offset_y_step_size_t1', None)
        }
        if self.offset_y_probe_site['t1_x'] == None:
            self.offset_y_probe_site['t1_x'] = self.offset_y_probe_site['t0_x']
        if self.offset_y_step_size['t1'] is None:
            self.offset_y_step_size['t1'] = self.offset_y_step_size['t0']
        
        self.offset_z_probe_site = {
            'x': config.getfloat('offset_z_probe_site_x', 125.0),
            'y': config.getfloat('offset_z_probe_site_y', 125.0),
            'z': config.getfloat('offset_z_probe_site_z', 0.0)
        }
        self.edge_threshold = config.getfloat('edge_threshold', 0.25)
        self.edge_probing_distance = config.getfloat('edge_probing_distance', 10)
        self.probe_differential = {
            'x': config.getfloat('probe_differential_x'),
            'y': config.getfloat('probe_differential_y', 0.0),
            'z': config.getfloat('probe_differential_z')
        }
        gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.start_gcode = gcode_macro.load_template(config, 'start_gcode', '')
        self.end_gcode = gcode_macro.load_template(config, 'end_gcode', '')
        self.query_probes = self.printer.load_object(config,
                                                       'query_named_probes')
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)
        self.printer.register_event_handler("homing:home_rails_end",
                                            self._handle_home_rails_end)
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command('CALIBRATE_IDEX_OFFSET_X', self.cmd_CALIBRATE_IDEX_OFFSET_X,
                                    desc=self.cmd_CALIBRATE_IDEX_OFFSET_X_help)
        self.gcode.register_command('CALIBRATE_IDEX_OFFSET_Y', self.cmd_CALIBRATE_IDEX_OFFSET_Y,
                                    desc=self.cmd_CALIBRATE_IDEX_OFFSET_Y_help)
        self.gcode.register_command('CALIBRATE_IDEX_OFFSET_Z', self.cmd_CALIBRATE_IDEX_OFFSET_Z,
                                    desc=self.cmd_CALIBRATE_IDEX_OFFSET_Z_help)
        self.gcode.register_command('CALIBRATE_IDEX_OFFSETS', self.cmd_CALIBRATE_IDEX_OFFSETS,
                                    desc=self.cmd_CALIBRATE_IDEX_OFFSETS_help)
    def get_status(self, eventtime):
        return {'last_offset_x': self.last_offsets['x'],
                'last_offset_y': self.last_offsets['y'],
                'last_offset_z': self.last_offsets['z']}
    def _handle_connect(self):
        # get probing settings
        probe = self.query_probes.get_active_probe()
        if probe is None:
            raise self.printer.config_error(ERROR_NO_PROBE)
        if self.samples is None:
            self.samples = probe.sample_count
    def _handle_home_rails_end(self, homing_state, rails):
        # get z homing position
        for rail in rails:
            logging.info("TRON: _handle_home_rails_end stepper: %s" % (rail.get_steppers()[0].get_name()))
            if rail.get_steppers()[0].is_active_axis('x'):
                if rail.get_name() == 'dual_carriage':
                    self.positions_endstop['dual_carriage'] = rail.get_homing_info().position_endstop
                    self.positions_min['dual_carriage'], self.positions_max['dual_carriage'] = rail.get_range()
                    logging.info("TRON: _handle_home_rails_end dual_carriage - position_max %i" % (self.positions_max['dual_carriage']))
                else:
                    self.positions_endstop['x'] = rail.get_homing_info().position_endstop
                    self.positions_min['x'], self.positions_max['x'] = rail.get_range()
                    logging.info("TRON: _handle_home_rails_end x - position_endstop %i" % (self.positions_endstop['x']))
            elif rail.get_steppers()[0].is_active_axis('z'):
                # get homing settings from z rail
                self.z_homing = rail.position_endstop
                if self.probing_speed is None:
                    self.probing_speed = rail.homing_speed
                if self.second_speed is None:
                    self.second_speed = rail.second_homing_speed
                if self.sample_retract_dist is None:
                    self.sample_retract_dist = rail.homing_retract_dist
                if self.position_min is None:
                    self.position_min = rail.position_min
    def _build_config(self):
        pass
    def set_dual_carriage(self, carriage):
        dual_carriage = self.printer.lookup_object('dual_carriage')
        if (not(dual_carriage.dc[0].is_active() == dual_carriage.dc[1].is_active() == True)
                    and dual_carriage.dc[carriage].is_active() is False):
            dual_carriage.toggle_active_dc_rail(carriage)
    def find_edge(self, gcmd, axis, starting_pos, ending_pos, step_size, threshold, initial_result):
        probe = self.query_probes.get_active_probe()
        pos = starting_pos
        sample_retract_dist = self.get_sample_retract_dist()
        probing_speed = self.get_probing_speed()
        positions = []
        while (step_size < 0 and pos >= ending_pos) or (step_size > 0 and pos <= ending_pos):
            if self.samples == 1:
                toolhead = self.printer.lookup_object('toolhead')
                position = probe._probe(probing_speed)
                positions.append(position)
            else:
                positions.append(probe.run_probe(gcmd, samples = self.samples, ignore_tolerance=True))
            self._move([None, None, position[2] + sample_retract_dist], self.lift_speed)
            if len(positions) >= 2:
                current_z_difference = abs(positions[-1][2]-positions[-2][2])
                historical_z_difference = abs(positions[-1][2]-initial_result[2])
                if current_z_difference > threshold or historical_z_difference > threshold:
                    break
            pos = pos + step_size
            if axis == 'x':
                self._move([pos, None, None], self.lift_speed)
            else:
                self._move([None, pos, None], self.lift_speed)
        if axis == 'x':
            # return the average of the last two x's
            return [(positions[-1][0]+positions[-2][0])/2]+positions[-1][1:]
        else:
            # return the average of the last two y's
            return [positions[-1][0]] + [(positions[-1][1]+positions[-2][1])/2] + positions[-1][2:]
    def activate_idex_carriage(self, carriage):
        self.set_dual_carriage(carriage) 
        self.query_probes.activate_idex_probe(carriage)
    def get_sample_retract_dist(self):
        if self.sample_retract_dist != None:
            return self.sample_retract_dist
        else:
            probe = self.query_probes.get_active_probe()
            return probe.sample_retract_dist
    def get_probing_speed(self):
        if self.probing_speed != None:
            return self.probing_speed
        else:
            probe = self.query_probes.get_active_probe()
            return probe.speed
    def find_edge_for_carriage(self, carriage, gcmd, axis):
        step_size_cutoff = 0.05
        if axis == 'x':
            
            if carriage == 0:
                step_size = self.offset_x_step_size['t0']
                starting_pos = self.offset_x_probe_site['t0_x'] - step_size
            else:
                step_size = self.offset_x_step_size['t1']
                starting_pos = self.offset_x_probe_site['t1_x'] - step_size
            if step_size > 0:
                ending_pos = starting_pos + self.edge_probing_distance
            else:
                ending_pos = starting_pos - self.edge_probing_distance
            y = self.offset_x_probe_site['y']
            self._move([starting_pos, y, self.clearance], self.lift_speed)
            gcmd.respond_info("find x edge (T%i) - getting initial reading at x=%.3f step size: %.3f" 
                              % (carriage, starting_pos, step_size))
            probe = self.query_probes.get_active_probe()
            initial_result = probe.run_probe(gcmd)
            self._move([None, None, self.clearance], self.lift_speed)
            while abs(step_size) > step_size_cutoff:
                gcmd.respond_info("find x edge (T%i) - starting at x=%.3f step size: %.3f" 
                                  % (carriage, starting_pos, step_size))
                position = self.find_edge(gcmd, 'x', starting_pos, ending_pos, step_size, 
                                          self.edge_threshold, initial_result)
                x = position[0]
                starting_pos = x - step_size * 2
                ending_pos = x + step_size * 2
                step_size = step_size / 2
                self._move([starting_pos, None, None], self.lift_speed)
            gcmd.respond_info("found x edge (T%i) at x=%.3f" % (carriage, x))
            return x
        else:
            if carriage == 0:
                step_size = self.offset_y_step_size['t0']
            else:
                step_size = self.offset_y_step_size['t1']
            starting_pos = self.offset_y_probe_site['y'] - step_size
            if step_size > 0:
                ending_pos = starting_pos + self.edge_probing_distance
            else:
                ending_pos = starting_pos - self.edge_probing_distance
            x = self.offset_y_probe_site["t%s_x" % (carriage)]
            self._move([x, starting_pos, self.clearance], self.lift_speed)
            gcmd.respond_info("find y edge (T%i) - getting initial reading at x=%.3f step size: %.3f" 
                                  % (carriage, starting_pos, step_size))
            probe = self.query_probes.get_active_probe()
            initial_result = probe.run_probe(gcmd)
            self._move([None, None, self.clearance], self.lift_speed)
            while abs(step_size) > step_size_cutoff:
                gcmd.respond_info("find y edge (T%i) - starting at y=%.3f step size: %.3f" 
                                  % (carriage, starting_pos, step_size))
                position = self.find_edge(gcmd, 'y', starting_pos, ending_pos, step_size, 
                                          self.edge_threshold, initial_result)
                y = position[1]
                starting_pos = y - step_size * 2
                ending_pos = y + step_size * 2
                step_size = step_size / 2
                self._move([None, starting_pos, None], self.lift_speed)
            gcmd.respond_info("found y edge (T%i) at y=%.3f" % (carriage, y))
            return y
    def find_surface(self, gcmd):
        probe = self.query_probes.get_active_probe()
        self._move([self.offset_z_probe_site['x'], self.offset_z_probe_site['y'], self.clearance], self.lift_speed)
        return probe.run_probe(gcmd)
        
    def park_carriage(self, carriage):
        self._move([None, None, 10], 1000)
        if carriage == 0:
            self._move([self.positions_endstop['x'] + 0.2, None, None], 1000)
        else:
            self._move([self.positions_max['dual_carriage'] - 0.2, None, None], 1000)
            
    def set_gcode_offset(self, offsets):
        gcode_move = self.printer.lookup_object('gcode_move')
        move_delta = [0., 0., 0.]
        for axis in range(3):
            offset = offsets[axis]
            if offset != None:
                delta = offset - gcode_move.homing_position[axis]
                move_delta[axis] = delta
                gcode_move.base_position[axis] += delta
                gcode_move.homing_position[axis] = offset

    def save_carriage_state(self):
        dual_carriage = self.printer.lookup_object('dual_carriage')
        if dual_carriage.dc[0].is_active():
            self.carriage_state['active_carriage'] = 0
        else:
            self.carriage_state['active_carriage'] = 1
        toolhead = self.printer.lookup_object('toolhead')
        self.carriage_state['toolhead_position'] = toolhead.get_position()
    def restore_carriage_state(self):
        if self.carriage_state['active_carriage'] == 0:
            self.park_carriage(1)
            self.activate_idex_carriage(0)
        
        self._move(self.carriage_state['toolhead_position'], 1000)
            
    def calibrate_idex_offsets(self, gcmd, skip=[]):
        self.save_carriage_state()
        self.activate_idex_carriage(0)
        self.set_gcode_offset([None if 'x' in skip else 0, 
                               None if 'y' in skip else 0, 
                               None])                       # we leave Z alone for T0        
        positions = []
        
        if 'z' not in skip:
            gcmd.respond_info("find surface (T0) at %s" % (self.offset_z_probe_site))
            positions.append(self.find_surface(gcmd))

        if 'x' not in skip:
            t0_x = self.find_edge_for_carriage(0, gcmd, 'x')
        if 'y' not in skip:
            t0_y = self.find_edge_for_carriage(0, gcmd, 'y')
        
        self.park_carriage(0)

        self.activate_idex_carriage(1)
        self.set_gcode_offset([None if 'x' in skip else 0, 
                               None if 'y' in skip else 0, 
                               None if 'z' in skip else 0])
        
        if 'z' not in skip:
            gcmd.respond_info("find surface (T1) at %s" % (self.offset_z_probe_site))         
            positions.append(self.find_surface(gcmd))
            self.last_offsets['z'] = positions[-1][2] - self.probe_differential['z'] - positions[-2][2]
        
        if 'x' not in skip:
            t1_x = self.find_edge_for_carriage(1, gcmd, 'x')
            self.last_offsets['x'] = t1_x - self.probe_differential['x'] - t0_x
        if 'y' not in skip:
            t1_y = self.find_edge_for_carriage(1, gcmd, 'y')
            self.last_offsets['y'] = t1_y - self.probe_differential['y'] - t0_y
        
        if gcmd.get_int('APPLY', 0):
            self.set_gcode_offset([None if 'x' in skip else self.last_offset_x, 
                                   None if 'y' in skip else self.last_offset_y,
                                   None if 'z' in skip else self.last_offset_z])
        
        self.restore_carriage_state()
         
    cmd_CALIBRATE_IDEX_OFFSET_X_help = ("Automatically calibrates the idex offset in x")
    def cmd_CALIBRATE_IDEX_OFFSET_X(self, gcmd):
        calibrate_idex_offsets(gcmd, skip=['y', 'z'])
        gcmd.respond_info("CALIBRATE_IDEX_OFFSET_X offset_x: %.3f" % (self.last_offsets['x']))
    
    cmd_CALIBRATE_IDEX_OFFSET_Y_help = ("Automatically calibrates the idex offset in y")
    def cmd_CALIBRATE_IDEX_OFFSET_Y(self, gcmd):
        calibrate_idex_offsets(gcmd, skip=['x', 'z'])
        gcmd.respond_info("CALIBRATE_IDEX_OFFSET_Y offset_y: %.3f" % (self.last_offsets['y']))
    
    cmd_CALIBRATE_IDEX_OFFSET_Z_help = ("Automatically calibrates the idex offset in z")
    def cmd_CALIBRATE_IDEX_OFFSET_Z(self, gcmd):
        calibrate_idex_offsets(gcmd, skip=['x', 'y'])
        gcmd.respond_info("CALIBRATE_IDEX_OFFSET_Z offset_z: %.3f" % (self.last_offsets['z']))

    cmd_CALIBRATE_IDEX_OFFSETS_help = ("Automatically calibrates idex offsets in x, y, and z")
    def cmd_CALIBRATE_IDEX_OFFSETS(self, gcmd):
        axes = gcmd.get('SKIP', '').lower()
        self.calibrate_idex_offsets(gcmd, skip=[] + (['x'] if 'x' in axes else []) + 
                                               (['y'] if 'y' in axes else []) +
                                               (['z'] if 'z' in axes else []))
        gcmd.respond_info("CALIBRATE_IDEX_OFFSETS offsets: [%.3f, %.3f, %.3f]" % 
                          (self.last_offsets['x'], self.last_offsets['y'], self.last_offsets['z']))
    
    def _move(self, coord, speed):
        self.printer.lookup_object('toolhead').manual_move(coord, speed)

def load_config(config):
    return IDEXOffsetsCalibrationHelper(config)
