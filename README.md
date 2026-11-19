# Hypersonic_Scramjet_Fadec_Thermal_Fullstack_Repo

Production service architecture and telemetry verification suite.

## Modules
- `backend/api/BoundaryLayerBleedSlot_telemetry_ingest_rest_dispatcher.py` (940 lines)
- `backend/api/fadec_triple_voter_websocket_streaming_multiplexer.py` (940 lines)
- `backend/api/oblique_shock_isolator_interlock_override_command_router.py` (940 lines)
- `backend/core/endothermic_cracker_bed_deterministic_state_machine_guard.py` (940 lines)
- `backend/core/oblique_shock_isolator_canonical_invariant_registry.py` (940 lines)
- `backend/core/regenerative_cooling_panel_cryptographic_frame_authenticator.py` (940 lines)
- `backend/domain/cavity_flameholder_strut_nonlinear_constitutive_equations.py` (940 lines)
- `backend/domain/inlet_cowl_actuator_thermodynamic_free_energy_jacobian.py` (940 lines)
- `backend/domain/plasma_torch_igniter_multiscale_perturbation_manifold.py` (940 lines)
- `backend/engines/air_data_flush_fads_mahalanobis_outlier_rejection_engine.py` (940 lines)
- `backend/engines/cmc_leading_edge_tile_extended_kalman_covariance_solver.py` (940 lines)
- `backend/engines/expansion_nozzle_sern_lyapunov_dissipative_controller.py` (940 lines)

## Running Tests
```bash
pytest -v
python -m unittest discover -s tests
```
