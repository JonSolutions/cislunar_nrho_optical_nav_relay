# Cislunar_Nrho_Optical_Nav_Relay_Fullstack_Repo

Production service architecture and telemetry verification suite.

## Modules
- `backend/api/cr3bp_nrho_manifold_interlock_override_command_router.py` (940 lines)
- `backend/api/ephemeris_de440_chebyshev_telemetry_ingest_rest_dispatcher.py` (940 lines)
- `backend/api/lunar_south_pole_relay_websocket_streaming_multiplexer.py` (940 lines)
- `backend/core/cr3bp_nrho_manifold_canonical_invariant_registry.py` (940 lines)
- `backend/core/hall_thruster_sep_bus_deterministic_state_machine_guard.py` (940 lines)
- `backend/core/xnav_optical_centroid_cryptographic_frame_authenticator.py` (940 lines)
- `backend/domain/optical_comm_fso_pat_nonlinear_constitutive_equations.py` (940 lines)
- `backend/domain/solar_radiation_srp_multiscale_perturbation_manifold.py` (940 lines)
- `backend/domain/unstable_floquet_mode_thermodynamic_free_energy_jacobian.py` (940 lines)
- `backend/engines/ccsds_bundle_dtn_node_lyapunov_dissipative_controller.py` (940 lines)
- `backend/engines/cislunar_sda_electro_opt_extended_kalman_covariance_solver.py` (940 lines)
- `backend/engines/dsac_atomic_clock_mahalanobis_outlier_rejection_engine.py` (940 lines)

## Running Tests
```bash
pytest -v
python -m unittest discover -s tests
```
