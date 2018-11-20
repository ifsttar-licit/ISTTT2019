"""
    Robustness analysis of MPC techniques over Truck platooning strategies.

    This script runs an scenario for the operational layer where 8 trucks in
    a platoon split.

    A 3rd order model is used + following options are feasible

    1. Analysis adding noise in the measurements.
    2. Model mismatch between the control and the model.
    3. Delays within the control signal.

    Usage:
    python platoon-closed-3rd.py
"""
from parameters import VehParameter, SimParameter
from models import VehNetwork, Vehicle, dynamic_3rd
from control import OperationalCtr, TacticalCtrl

from matplotlib import pyplot as plt

# Length of the platoon
N_VEH = 8

# Create a simulation timings
T_STP = 0.01
T_HOR = 0.5
T_SIM = 60

sim_par = SimParameter(T_STP, T_HOR, T_SIM)

# Create a vehicle model / Provided originally by the simulator
U_FFS = 25.0
K_X = 0.16
W_CGT = 6.25
L_VEH = 4.0

S0 = 10.0

veh_par = VehParameter.VehParameterSym(U_FFS, K_X, W_CGT, L_VEH)

# Create list of vehicles
veh_list = [Vehicle(sim_par, veh_par, dynamic_3rd, id=i) for i in range(N_VEH)]

# Create the network of vehicles
veh_network = VehNetwork(sim_par, veh_list)
print(vars(veh_network))

# Initialize the network of vehicles
x0 = [0.0] * N_VEH
s0 = [S0] * N_VEH
v0 = [U_FFS] * N_VEH
e0 = [0.0] * N_VEH
a0 = [0.0] * N_VEH  # only 3rd order models

# Initialize each vehicle
state0 = [s0, v0, e0, a0]
state0veh = list(zip(*state0))  # state each vehicle
state0net = dict(zip(veh_network.veh_currentids, state0veh))
veh_network.initialize_vehicles(state0net)

# Create the controller[]
op_ctrl = OperationalCtr()
event = {1: {'ta': 1, 'tm': 2, 'tau0': 1, 'tauf': 2}}
tc_ctrl = TacticalCtrl(veh_network.sim_par, event)
for k, v in tc_ctrl:
    print(k, v)
    #
tc_ctrl.compute_reference()

# print(len(sim_par.get_simtime_vector()))
# tc_ctrl.compute_reference()

# veh_network.launch_simulation()

# Scenario 1: Perturb the measurements with noise

# Scenario 2: Perturb the model parameters with noise / Parameters should keep consistancy

# Scenario 3: Save on each vehicle the full buffer but remember to retrieve the info in the past. Compute ctrl with this info and then feed the system.

# Obtain Performance measurements

# In all cases: Dynamic Answer (State + Control)

# Save results
