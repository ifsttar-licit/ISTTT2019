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

import os
import numpy as np

# Platoon length
N = 8

# CAV parameters
L_AVG = 4.75
T_LAG = 0.2

# Control
C_N1 = 0.1
C_N2 = 1
C_N3 = 0.5
U_MAX = 1.5  # Max. Acceleration
U_MIN = -1.5  # Min. Acceleration

# Time
DT = 0.1
H = 50  # samples horizon
SIMTIME = 90  # seconds
nSamples = int(SIMTIME * 1 / DT)
aDims = (nSamples, N)
aDimMPC = (H, N)

# Traffic
V_F = 25.0  # Max speed.
V_P = 20.0  # Platoon free flow
E = 25.0*0.3  # Speed drop for relaxation
C = 2400 / 3600.0
G_X = 1.5

# -----------------------------------------------------------------------------
# Simulation
# -----------------------------------------------------------------------------


def compute_parameters(g_x, c):
    """ Compute dynamically parameters based on G_X,C"""
    s_x = L_AVG + g_x
    k_x = 1 / s_x
    k_c = c / V_P
    w = c / (k_x - k_c)
    return (s_x, k_x, k_c, w)


S_X, K_X, K_C, W = compute_parameters(G_X, C)
TAU = 1/(K_X*W)  # Time shift
RTE = TAU * (V_P+W) / V_P  # Time headway

# At capacity
K = C/V_P
S_D = 1/K-L_AVG
G_T = S_D/V_P


def set_initial_condition(mS0, mV0, mDV0, mA0):
    """ Setup initial conditions of experiment"""
    mS, mV, mDV, mA = (np.zeros(aDims) for _ in range(4))
    mS[0, :] = mS0
    mV[0, :] = mV0
    mDV[0, :] = mDV0
    mA[0, :] = mA0
    return (mS, mV, mDV, mA)


def create_ref(dEvent, Teq):
    """Creates a reference matrix for the control"""

    def anticipation_time(T_0, T_F):
        """Computes the anticipation time according to TRB 2018"""
        T_a = E / 2 * (U_MIN-U_MAX) / (U_MIN * U_MAX) + \
            (V_P + W) / E * (T_F - T_0)
        return T_a

    def get_sigmoid(v0, vf, yld, ant):
        """ Computes a sigmoid function with rise time equivalent to anticipation time"""
        aNewTime = 8 * (aTime - (yld + ant/2)) / ant
        return v0 + (vf-v0) * 1 / (1 + np.exp(- aNewTime))

    mRef = np.ones(aDims) * Teq
    for event in dEvent:
        iIdTruck = event['id']
        fMrgTime = event['tm']
        T_0, T_X = event['tg']
        _T_0, _T_X = (T_X, T_0) if T_0 > T_X else (T_0, T_X)
        fAntTime = anticipation_time(_T_0, _T_X)
        fYldTime = fMrgTime - fAntTime

        mRef[:, iIdTruck] = get_sigmoid(T_0, T_X, fYldTime, fAntTime)

        print(f'Anticipation time: {fAntTime}')
        print(f'Yielding time: {fYldTime}')

    return mRef

# -----------------------------------------------------------------------------
# Control
# -----------------------------------------------------------------------------


def initialize_mpc(mS0, mV0, mDV0, mA0):
    """ Initialize internal variables control"""
    m_S, m_V, m_DV, m_A, m_LS, m_LV, m_LA, = (
        np.zeros(aDimMPC) for _ in range(7))
    m_S[0] = mS0
    m_V[0] = mV0
    m_DV[0] = mDV0
    m_A[0] = mDV0
    return m_S, m_V, m_DV, m_A, m_LS, m_LV, m_LA


def forward_evolution(X, U, lag = T_LAG):
    """ Compute forward model evolution
        X: S, V, DV, A 
        U: control
    """

    S, V, DV, A = X

    def cordim(x): return x.shape if len(x.shape) > 1 else (1, x.shape[0])

    U = U.reshape(cordim(U))

    for i, u in enumerate(U):
        if i < len(S)-1:
            da = np.hstack((0, A[i][0:-1]-A[i][1:]))
            DV[i+1] = DV[i] + DT * da
            S[i+1] = S[i] + DT * DV[i]
            V[i+1] = V[i] + DT * A[i]
            A[i+1] = (1-DT/lag) * A[i] + DT / lag * u
    return S, V, DV, A


def backward_evolution(X, Ref, lag = T_LAG):
    """ Compute  bakckward costate evolution
        L: LS, LV, LA
        X: S, V, DV, A 
    """

    def reversedEnumerate(*args):
        """ Inverse enumeration iterator"""
        revArg = [np.flip(x, axis=0) for x in args]
        return zip(range(len(args[0])-1, -1, -1), *revArg)

    S, V, DV, A = X

    ls, lv, la = (np.zeros(aDimMPC) for _ in range(3))

    runinv = reversedEnumerate(S, V, DV, A, Ref)

    for i, s, v, dv, _, tg in runinv:
        if i > 0:
            sref = v * tg + L_AVG
            lv[i-1] = lv[i] + DT * (-2 * C_N1 * (s-sref) * tg
                                    - 2 * C_N2 * dv - ls[i]
                                    )
            ls[i-1] = ls[i] + DT * (2 * C_N1 * (s-sref)
                                    )

            la[i-1] = la[i] + DT * (lv[i] - la[i]/lag)

    return ls, lv, la


def compute_control(mX0, mRef, lag = T_LAG):
    """ Computes a control based on mX0 and the reference mRef"""

    _m_S, _m_V, _m_DV, _m_A, _m_LS, _m_LV, _m_LA = initialize_mpc(*mX0)
    _X = (_m_S, _m_V, _m_DV, _m_A)

    # Parameters

    ALPHA = 0.02
    EPS = 0.1

    # Convergence
    error = 100
    bSuccess = 2
    N = 10000  # number of iterations

    step = iter(range(N))
    n = 0
    n_prev = 0

    while (error > EPS) and (bSuccess > 0):
        try:
            next(step)

            U_star = -_m_LA / (2 * C_N3 * lag)

            U_star = np.clip(U_star, U_MIN, U_MAX)

            _m_S, _m_V, _m_DV, _m_A = forward_evolution(_X, U_star, lag)

            _lS, _lV, _lA = backward_evolution(_X, mRef, lag)

            _m_LS = (1 - ALPHA) * _m_LS + ALPHA * _lS
            _m_LV = (1 - ALPHA) * _m_LV + ALPHA * _lV
            _m_LA = (1 - ALPHA) * _m_LA + ALPHA * _lA

            error = np.linalg.norm(_m_LS - _lS) + \
                np.linalg.norm(_m_LV - _lV) + \
                np.linalg.norm(_m_LA - _lA)

            # print(f'Error:{error}')
            # Routine for changing convergence parameter

            if error > 10e5:
                raise AssertionError('Algorithm does not converge ')
            if n >= 5000:
                ALPHA = max(ALPHA - 0.01, 0.01)
                print(f'Reaching {n} iterations: Reducing alpha: {ALPHA}')
                print(f'Error before update {error}')
                if n > 20000:
                    raise AssertionError(
                        'Maximum iterations reached by the algorithm')
                n_prev = n + n_prev
                n = 0
            if error <= EPS:
                bSuccess = 0

            n += 1

        except StopIteration:
            print('Stop by iteration')
            print('Last simulation step at iteration: {}'.format(n+n_prev))
            bSuccess = 0

    n = n + n_prev
    print(f'Total iterations:{n}')

    return U_star[0]


def closed_loop(dEvent):
    """Receives a dictionary and finds the solution in closed loop"""

    # Robustness
    control_lag = dEvent[0].get('lag',T_LAG) # Parameter known by controller

    par_unc_const = dEvent[0].get('mdlt',0) # Constant Perturb

    # Time
    aTime = np.arange(nSamples)*DT

    mS0 = np.ones(N) * (S_D + L_AVG)
    mV0 = np.ones(N) * V_P
    mDV0 = np.zeros(N)
    mA0 = np.zeros(N)
    mX0 = np.array([i * (S_D + L_AVG) for i in reversed(range(N))])

    mS, mV, mDV, mA = set_initial_condition(mS0, mV0, mDV0, mA0)
    mX = np.empty_like(mS)
    mX[0] = mX0

    mRef = create_ref(dEvent, G_T)
    mU = np.zeros(mRef.shape)

    mRefW = G_T*np.ones((H, N))

    for i, t in enumerate(zip(mRef, aTime)):

        if i < len(mRef)-2:

            mRefW = mRef[i:min(i+H, nSamples), :]

            print(f'Sample Time:{t[-1]}')

            aX = (mS[i], mV[i], mDV[i], mA[i])
            if dEvent[0]['ns']:
                aX = (mS[i] + dEvent[0]['w']*np.random.rand(N),
                      mV[i],
                      mDV[i],
                      mA[i])
            if dEvent[0]['delay']:
                d = max(dEvent[0]['d'],0)
                aX = (mS[i-d],
                      mV[i-d],
                      mDV[i-d],
                      mA[i-d])

            par_unc_var = dEvent[0].get('t_lag_v',0)
            par_unc_rnd = (np.random.rand(1)[0] - 1) * \
                        par_unc_var 
            real_lag = control_lag + par_unc_const + par_unc_rnd

            print(f"Lag control: {control_lag} / Real lag: {real_lag} / Unc: {par_unc_rnd}")

            aU = compute_control(aX, mRefW, control_lag)

            aDA = mA[i][0:-1] - mA[i][1:]

            aDA = np.insert(aDA, 0, 0)



            mS[i+1] = mS[i] + DT * mDV[i]
            mV[i+1] = mV[i] + DT * mA[i]
            mDV[i+1] = mDV[i] + DT * aDA
            mA[i+1] = (1-DT/real_lag) * mA[i] + DT/real_lag * aU

            mU[i] = aU

            mX[i+1] = mX[i] + mV[i] * DT + 0.5 * aU * DT ** 2

    mSd = mRef * V_P + L_AVG

    return mS, mV, mDV, mA, mSd, mU, mX


if __name__ == "__main__":

    # Time
    aTime = np.arange(nSamples)*DT

    # Parameters configuration 
    # 
    # id: Id of vehicle splitting (starting from 0-N)
    # tm: Merging time 
    # tg: time gap 
    # ns: true/false - add noise on the spacing sensor of variance 'w', 
    # if 'true' w should be specified
    # 
    # w: variance of noise on spacing measurement 
    # mdlt: model missmatch: Time value added to the T_LAG parameter
    # delay: true /false introduces delay on the system, default = 0:
    # 
    # d: delay value 
    #
    # mldtn: model mismatch with noisy condition: Time constant is T_LAG
    #   
    # Simulated events 
    # 0. Opening gap 1 T -> 2 T | Lag = 200ms
    # 1. Opening gap 1 T -> 3 T | Lag = 200ms
    # 2. Opening gap 1 T -> 2 T | Lag = 200ms | Noisy conditions
    # 3. Opening gap 1 T -> 3 T, 1 T -> 2T | Lag = 200ms
    # 4. Opening gap 1 T -> 3 T, 1 T -> 2T | Lag = 200ms | Noisy conditions
    # 5. Opening gap 1 T -> 2 T | Lag = 300ms  
    # 6. Opening gap 1 T -> 2 T | Lag = 400ms  
    # 7. Opening gap 1 T -> 2 T | Lag = 500ms  
    # 8. Opening gap 1 T -> 2 T | Lag = 600ms      
    # 9. `Opening gap 1 T -> 2 T | Model missmatch 
    # 10. Opening gap 1 T -> 2 T | Model missmatch | Noisy conditions
    # 11. Opening gap 1 T -> 2 T | Delay = 100ms  
    # 12. Opening gap 1 T -> 2 T | Delay = 200ms  
    # 13. Opening gap 1 T -> 2 T | Delay = 300ms  
    # 14. Opening gap 1 T -> 2 T | Delay = 400ms      
    # 15. Opening gap 1 T -> 2 T | Delay = 500ms  
    # 16. Opening gap 1 T -> 2 T | Delay = 600ms  
    # 17. Opening gap 1 T -> 2 T | Delay = 800ms  
    # 18. Opening gap 1 T -> 2 T | Delay = 1000ms  
    # 19. Opening gap 1 T -> 2 T | Delay = 1200ms     
    # 20. Opening gap 1 T -> 2 T | Delay = 1400ms 
    # 21. Opening gap 1 T -> 1.9 T, 1 T -> 2.2 T| Lag = 200ms 
    # 22. Opening gap 1 T -> 2 T | Lag = 200ms
    # 23. Opening gap 1 T -> 2 T | Lag = 300ms 
    # 24. Opening gap 1 T -> 2 T | Lag = 100ms | Model missmatch = 200ms
    # 25. Opening gap 1 T -> 2 T | Lag = 300ms | Model missmatch = -200ms
    # Model Missmatch (MM) -  Random Model Missmatch (RMM)
    # 26. Opening gap 1 T -> 2 T | Lag = 200ms | MM = 0 | RMM = 150ms
    # 27. Opening gap 1 T -> 2 T | Lag = 300ms | MM = 0 | RMM = 250ms
    # 28. Opening gap 1 T -> 2 T | Lag = 400ms | MM = 0 | RMM = 350ms
    # 29. Opening gap 1 T -> 2 T | Lag = 100ms | MM = 200ms | RMM = 200ms 
    # 30. Opening gap 1 T -> 2 T | Lag = 500ms | MM = 0 | RMM = 450ms  
    # 31. Opening gap 1 T -> 2 T | Lag = 500ms | MM = 0 | RMM = 150ms
    # 28. Opening gap 1 T -> 2 T | Lag = 500ms | MM = 0 | RMM = 0ms    
    #  
    mEvents =  [({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'lag': T_LAG,
                 'delay': False,
                 'name': '1T2TL200'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 3 * G_T),
                 'ns': False,
                 'lag': T_LAG,
                 'delay': False,
                 'name': '1T3TL200'},
                ),
               ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': True,
                 'w': 1, 
                 'lag': T_LAG,
                 'delay': False,
                 'name': '1T2TL200N'},
                ),              
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 3 * G_T),
                 'ns': False,
                 'w': 0,  
                 'lag': T_LAG,
                 'delay': False,
                 'name': '1T3T1T2TL200'},
                 {'id': 4,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'w': 0, 
                 'lag': T_LAG,
                 'delay': False},
                ),      
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 3 * G_T),
                 'ns': True,
                 'w': 1,  
                 'lag': T_LAG,
                 'delay': False,
                 'name': '1T3T1T2TL200N'},
                 {'id': 4,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': True,
                 'w': 1, 
                 'lag': T_LAG,
                 'delay': False},
                ),                               
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'lag': 0.3,
                 'delay': False,
                 'name': '1T2TL300'},
                ), 
                 ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'lag': 0.4,
                 'delay': False,
                 'name': '1T2TL400'},
                ), 
                 ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'lag': 0.5,
                 'delay': False,
                 'name': '1T2TL500'},
                ), 
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'lag': 0.6,
                 'delay': False,
                 'name': '1T2TL600'},
                ), 
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'w': 0, 
                 'mdlt': 0.2, 
                 'lag': T_LAG,
                 'delay': False,
                 'name': '1T2TL200M'},
                ),                
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': True,
                 'w': 1, 
                 'mdlt': 0.2, 
                 'lag': T_LAG,
                 'delay': False,
                 'name': '1T2TL200MN'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'lag': T_LAG,
                 'delay': True,
                 'd': 1,
                 'name': '1T2TL200D1'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'lag': T_LAG,
                 'delay': True,
                 'd': 2,
                 'name': '1T2TL200D2'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'lag': T_LAG,
                 'delay': True,
                 'd': 3,
                 'name': '1T2TL200D3'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'lag': T_LAG,
                 'delay': True,
                 'd': 4,
                 'name': '1T2TL200D4'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'lag': T_LAG,
                 'delay': True,
                 'd': 5,
                 'name': '1T2TL200D5'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'lag': T_LAG,
                 'delay': True,
                 'd': 6,
                 'name': '1T2TL200D6'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'lag': T_LAG,
                 'delay': True,
                 'd': 8,
                 'name': '1T2TL200D8'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'lag': T_LAG,
                 'delay': True,
                 'd': 10,
                 'name': '1T2TL200D10'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'lag': T_LAG,
                 'delay': True,
                 'd': 12,
                 'name': '1T2TL200D12'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'lag': T_LAG,
                 'delay': True,
                 'd': 14,
                 'name': '1T2TL200D14'},
                ),
                ({'id': 3,
                 'tm': 56.26,
                 'tg': (G_T, 1.9*G_T),
                 'ns': False,
                 'w': 0,  
                 'lag': T_LAG,
                 'delay': False,
                 'name': '1T19T1T222TL200'},
                 {'id': 5,
                 'tm': 58.78,
                 'tg': (G_T, 2.2*G_T),
                 'ns': False,
                 'w': 0, 
                 'lag': T_LAG,
                 'delay': False},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'w': 0,  
                 'lag': 0.2,
                 'delay': False,
                 'name': '1T2TL200M'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'w': 0,  
                 'lag': 0.3,
                 'delay': False,
                 'name': '1T2TL300M'},
                ),                
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'w': 0, 
                 'mdlt': 0.2, 
                 'lag': 0.1,
                 'delay': False,
                 'name': '1T2TL100M200'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'w': 0, 
                 'mdlt': -0.2, 
                 'lag': 0.3,
                 'delay': False,
                 'name': '1T2TL300M200'},
                ),                
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'w': 0,  
                 't_lag_v': 0.15,
                 'lag': 0.2,
                 'delay': False,
                 'name': '1T2TL200MN200'},
                ), 
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'w': 0,  
                 't_lag_v': 0.25,
                 'lag': 0.3,
                 'delay': False,
                 'name': '1T2TL300MN300'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'w': 0,  
                 't_lag_v': 0.35,
                 'lag': 0.4,
                 'delay': False,
                 'name': '1T2TL400MN400'},
                ),                                    
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'w': 0,  
                 'mdlt': 0.2,                  
                 't_lag_v': 0.2,
                 'lag': 0.1,
                 'delay': False,
                 'name': '1T2TL100M200N200'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'w': 0,  
                 't_lag_v': 0.45,
                 'lag': 0.5,
                 'delay': False,
                 'name': '1T2TL500MN500'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'w': 0,  
                 't_lag_v': 0.1,
                 'lag': 0.5,
                 'delay': False,
                 'name': '1T2TL500MN100'},
                ),
                ({'id': 1,
                 'tm': 30.0,
                 'tg': (G_T, 2 * G_T),
                 'ns': False,
                 'w': 0,
                 'lag': 0.5,
                 'delay': False,
                 'name': '1T2TL500M'},
                ),
                ]
    # New tests 
    # mEvents =   [
    #             ]

    print(f'Simulating the following situations: {mEvents}\n')

    dirname = os.path.join(os.getcwd(), '..', 'Output')

    for ev_id, event in enumerate(mEvents):

        print(f'Current situation:{event}\n')

        S, V, DV, A, Sd, U, X = closed_loop(event)

        print(f'Event simulated ')

        sEvent = '_3rd_yield_' + event[0]['name']

        filename_S = dirname + os.path.sep + 'space' + sEvent + '.csv'
        filename_V = dirname + os.path.sep + 'speed' + sEvent + '.csv'
        filename_A = dirname + os.path.sep + 'accel' + sEvent + '.csv'
        filename_R = dirname + os.path.sep + 'refer' + sEvent + '.csv'
        filename_U = dirname + os.path.sep + 'cntrl' + sEvent + '.csv'
        filename_X = dirname + os.path.sep + 'posit' + sEvent + '.csv'

        np.savetxt(filename_S, S, fmt='%.6f',
                   delimiter='\t', newline='\n')
        np.savetxt(filename_V, V, fmt='%.6f',
                   delimiter='\t', newline='\n')
        np.savetxt(filename_R, Sd,fmt='%.6f', 
                   delimiter='\t', newline='\n')
        np.savetxt(filename_U, U, fmt='%.6f',
                   delimiter='\t', newline='\n')
        np.savetxt(filename_X, X, fmt='%.6f',
                   delimiter='\t', newline='\n')
        np.savetxt(filename_A, A, fmt='%.6f',
                   delimiter='\t', newline='\n')
