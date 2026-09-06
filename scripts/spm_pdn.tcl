# Macro pins end on Metal4. Keep core TopMetal1/2 straps over the macro,
# and bridge them through a local horizontal Metal5 mesh.
add_global_connection -net VDD -pin_pattern {^VDD!$} -power
add_global_connection -net VDD -pin_pattern {^VDDARRAY!$} -power
add_global_connection -net VSS -pin_pattern {^VSS!$} -ground
source $::env(PLATFORM_DIR)/pdn.tcl
define_pdn_grid -name sram_grid -macro -cells {RM_IHPSG13_1P_1024x32_c2_bm_bist} -halo {2 2} -grid_over_boundary -obstructions {Metal1 Metal2 Metal3 Metal4 Metal5}
add_pdn_stripe -grid sram_grid -layer Metal5 -width 2.2 -pitch 20 -offset 5
add_pdn_connect -grid sram_grid -layers {Metal4 Metal5}
add_pdn_connect -grid sram_grid -layers {Metal5 TopMetal1}
