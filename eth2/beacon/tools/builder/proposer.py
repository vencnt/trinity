from typing import Dict, Sequence, Type

from eth_typing import BLSPubkey, BLSSignature

from eth2.beacon.committee_helpers import get_beacon_proposer_index
from eth2.beacon.constants import EMPTY_SIGNATURE
from eth2.beacon.exceptions import ProposerIndexError
from eth2.beacon.helpers import compute_epoch_at_slot
from eth2.beacon.signature_domain import SignatureDomain
from eth2.beacon.state_machines.base import BaseBeaconStateMachine
from eth2.beacon.tools.builder.validator import sign_transaction
from eth2.beacon.types.attestations import Attestation
from eth2.beacon.types.blocks import (
    BaseBeaconBlock,
    BaseSignedBeaconBlock,
    BeaconBlock,
    BeaconBlockBody,
    SignedBeaconBlock,
)
from eth2.beacon.types.deposits import Deposit
from eth2.beacon.types.eth1_data import Eth1Data
from eth2.beacon.types.states import BeaconState
from eth2.beacon.typing import (
    EpochOperation,
    FromBlockParams,
    Root,
    Slot,
    ValidatorIndex,
    default_validator_index,
)
from eth2.configs import Eth2Config


def is_proposer(
    state: BeaconState, validator_index: ValidatorIndex, config: Eth2Config
) -> bool:
    """
    Return if the validator is proposer of `state.slot`.
    """
    return get_beacon_proposer_index(state, config) == validator_index


def _generate_randao_reveal(
    privkey: int, slot: Slot, state: BeaconState, config: Eth2Config
) -> BLSSignature:
    """
    Return the RANDAO reveal for the validator represented by ``privkey``.
    The current implementation requires a validator to provide the BLS signature
    over the SSZ-serialized epoch in which they are proposing a block.
    """
    epoch = compute_epoch_at_slot(slot, config.SLOTS_PER_EPOCH)

    randao_reveal = sign_transaction(
        operation=EpochOperation(epoch),
        privkey=privkey,
        state=state,
        slot=slot,
        signature_domain=SignatureDomain.DOMAIN_RANDAO,
        slots_per_epoch=config.SLOTS_PER_EPOCH,
    )
    return randao_reveal


def validate_proposer_index(
    state: BeaconState, config: Eth2Config, slot: Slot, validator_index: ValidatorIndex
) -> None:
    beacon_proposer_index = get_beacon_proposer_index(state.copy(slot=slot), config)

    if validator_index != beacon_proposer_index:
        raise ProposerIndexError


def create_block_proposal(
    slot: Slot,
    parent_root: Root,
    randao_reveal: BLSSignature,
    eth1_data: Eth1Data,
    state: BeaconState,
    state_machine: BaseBeaconStateMachine,
    config: Eth2Config,
) -> BeaconBlock:
    future_state = state_machine.state_transition.apply_state_transition(
        state, future_slot=slot
    )
    proposer_index = get_beacon_proposer_index(future_state, config)
    proposal = BeaconBlock.create(
        slot=slot,
        parent_root=parent_root,
        body=BeaconBlockBody.create(randao_reveal=randao_reveal, eth1_data=eth1_data),
        proposer_index=proposer_index,
    )
    signed_block = SignedBeaconBlock.create(message=proposal, signature=EMPTY_SIGNATURE)
    post_state, signed_block = state_machine.import_block(
        signed_block, state, check_proposer_signature=False
    )
    return signed_block.message


def create_unsigned_block_on_state(
    *,
    state: BeaconState,
    config: Eth2Config,
    block_class: Type[BaseBeaconBlock],
    parent_block: BaseBeaconBlock,
    slot: Slot,
    attestations: Sequence[Attestation],
    eth1_data: Eth1Data = None,
    deposits: Sequence[Deposit] = None,
    proposer_index: ValidatorIndex = default_validator_index,
) -> BeaconBlock:
    """
    Create a beacon block with the given parameters.
    """
    block_params = FromBlockParams(slot=slot, proposer_index=proposer_index)
    block = block_class.from_parent(
        parent_block=parent_block, block_params=block_params
    )

    # MAX_ATTESTATIONS
    attestations = attestations[: config.MAX_ATTESTATIONS]

    # TODO: Add more operations
    if eth1_data is None:
        eth1_data = state.eth1_data
    body = BeaconBlockBody.create(eth1_data=eth1_data, attestations=attestations)
    if deposits is not None and len(deposits) > 0:
        body = body.set("deposits", deposits)

    block = block.set("body", body)

    return block


def create_block_on_state(
    *,
    state: BeaconState,
    config: Eth2Config,
    state_machine: BaseBeaconStateMachine,
    signed_block_class: Type[BaseSignedBeaconBlock],
    parent_block: BaseBeaconBlock,
    slot: Slot,
    proposer_index: ValidatorIndex,
    privkey: int,
    attestations: Sequence[Attestation],
    eth1_data: Eth1Data = None,
    deposits: Sequence[Deposit] = None,
    check_proposer_index: bool = True,
) -> SignedBeaconBlock:
    """
    Create a beacon block with the given parameters.
    """
    if check_proposer_index:
        validate_proposer_index(state, config, slot, proposer_index)

    block_class = signed_block_class.block_class
    block = create_unsigned_block_on_state(
        state=state,
        config=config,
        block_class=block_class,
        parent_block=parent_block.message,
        slot=slot,
        proposer_index=proposer_index,
        attestations=attestations,
        eth1_data=eth1_data,
        deposits=deposits,
    )

    # Randao reveal
    randao_reveal = _generate_randao_reveal(privkey, slot, state, config)
    block = block.set("body", block.body.set("randao_reveal", randao_reveal))

    # Apply state transition to get state root
    signed_block = signed_block_class.create(message=block, signature=EMPTY_SIGNATURE)
    post_state, signed_block = state_machine.import_block(
        signed_block, state, check_proposer_signature=False
    )

    # Sign
    signature = sign_transaction(
        operation=signed_block.message,
        privkey=privkey,
        state=post_state,
        slot=slot,
        signature_domain=SignatureDomain.DOMAIN_BEACON_PROPOSER,
        slots_per_epoch=config.SLOTS_PER_EPOCH,
    )
    signed_block = signed_block.set("signature", signature)

    return signed_block


def advance_to_slot(
    state_machine: BaseBeaconStateMachine, state: BeaconState, slot: Slot
) -> BeaconState:
    # advance the state to the ``slot``.
    state_transition = state_machine.state_transition
    state = state_transition.apply_state_transition(state, future_slot=slot)
    return state


def create_mock_block(
    *,
    state: BeaconState,
    config: Eth2Config,
    state_machine: BaseBeaconStateMachine,
    signed_block_class: Type[BaseSignedBeaconBlock],
    parent_block: BaseSignedBeaconBlock,
    keymap: Dict[BLSPubkey, int],
    slot: Slot = None,
    attestations: Sequence[Attestation] = (),
) -> BaseSignedBeaconBlock:
    """
    Create a mocking block at ``slot`` with the given block parameters and ``keymap``.

    Note that it doesn't return the correct ``state_root``.
    """
    future_state = advance_to_slot(state_machine, state, slot)
    proposer_index = get_beacon_proposer_index(future_state, config)
    proposer_pubkey = state.validators[proposer_index].pubkey
    proposer_privkey = keymap[proposer_pubkey]

    result_block = create_block_on_state(
        state=future_state,
        config=config,
        state_machine=state_machine,
        signed_block_class=signed_block_class,
        parent_block=parent_block,
        slot=slot,
        proposer_index=proposer_index,
        privkey=proposer_privkey,
        attestations=attestations,
        check_proposer_index=False,
    )

    return result_block
