# Automatically determines the z location of a magnetic probe dock
#
# Copyright (C) 2022  Tron Fu <tron@riverwatcher.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging

class CalibrateDocklocationZ:
    def __init__(self, config):
        self.state = None
        self.config = config
        self.printer = config.get_printer()
        self.speed = config.getfloat('speed', 50.0)
        self.docklocation_z_max = config.getfloat('docklocation_z_max', None)
        self.docklocation_z_min = config.getfloat('docklocation_z_min', None)
        self.docklocation_z_offset = config.getfloat('docklocation_z_offset', 0)
        self.docklocation_z = ''
        gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command('CALIBRATE_DOCKLOCATION_Z', self.cmd_CALIBRATE_DOCKLOCATION_Z,
                                    desc=self.cmd_CALIBRATE_DOCKLOCATION_Z_help)
    
    cmd_CALIBRATE_DOCKLOCATION_Z_help = ("Automatically find the docklocation_z"
                            " for the Klicky probe")
    def cmd_CALIBRATE_DOCKLOCATION_Z(self, gcmd):
        logging.info("Starting CALIBRATE_DOCKLOCATION_Z")
        self.gcode.respond_info("CALIBRATE_DOCKLOCATION_Z")
        if self.state is not None:
            raise self.printer.command_error("Already performing FIND_DOCKLOCATION_Z")
            return
        docklocation_x = gcmd.get_float('X')
        docklocation_y = gcmd.get_float('Y')
        self.docklocation_z = ''
        # move to Klicky probe dock position
        gcmd.respond_info("CALIBRATE_DOCKLOCATION_Z moving to X:%.3f Y:%.3f Z:%.3f S:%.3f" 
                          % (docklocation_x, docklocation_y, self.docklocation_z_max, self.speed))
        self._move([None, None, self.docklocation_z_max], self.speed)
        self._move([None, docklocation_y, None], self.speed)
        self._move([docklocation_x, None, None], self.speed)
        z = self.docklocation_z_max
        while z >= self.docklocation_z_min and not self.docklocation_z:
            ##  lower toolhead
            self._move([None, None, z], self.speed)
            gcmd.respond_info("CALIBRATE_DOCKLOCATION_Z probing at z: %.3f" %  (z))
            if not self._query_probe():
                self.docklocation_z = z + self.docklocation_z_offset
                gcmd.respond_info("CALIBRATE_DOCKLOCATION_Z probe found at z: %.3f" %  (z))
            z -= 1
            
    def get_status(self, eventtime):
        return {'docklocation_z': self.docklocation_z}        
    
    def _query_probe(self):
        toolhead = self.printer.lookup_object('toolhead')
        time = toolhead.get_last_move_time()
        probe = self.printer.lookup_object('probe')
        return probe.mcu_probe.query_endstop(time)
        
    def _move(self, coord, speed):
        self.printer.lookup_object('toolhead').manual_move(coord, speed)
def load_config(config):
    return CalibrateDocklocationZ(config)