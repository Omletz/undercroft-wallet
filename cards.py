from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Card:
    card_id: str
    tier: str
    name: str
    sector: str
    description: str


SET_1_CARDS: List[Card] = [
    # =========================================================================
    # SECRET RARES (5 cards: S1-SR-000 to S1-SR-004)
    # =========================================================================
    Card(
        card_id="S1-SR-000",
        tier="secret_rare",
        name="Genesis Core: Node Zero",
        sector="Depth 00 // Origin Bedrock",
        description="The unmapped terminal that broadcast the original block header. Enclosed entirely in naturally grown silicon carbide; still accepting inbound handshakes.",
    ),
    Card(
        card_id="S1-SR-001",
        tier="secret_rare",
        name="Zero-Volt Singularity",
        sector="Depth 40 // Mainframe Basement Void",
        description="An optical junction pulling current from ambient static without a physical bus connection. The amber telemetry readout refuses to calculate its resistance.",
    ),
    Card(
        card_id="S1-SR-002",
        tier="secret_rare",
        name="Quantum Spin Well",
        sector="Depth 33 // Isolation Sink",
        description="A localized magnetic pocket trapped inside a synthetic diamond wafer. Polarity flips continuously without thermal decay.",
    ),
    Card(
        card_id="S1-SR-003",
        tier="secret_rare",
        name="Radiant Siliconite Geode",
        sector="Depth 35 // Sub-Bedrock Fault",
        description="A cracked hollow core lined with razor-sharp semiconductor crystals, buzzing faintly at 14.318 MHz.",
    ),
    Card(
        card_id="S1-SR-004",
        tier="secret_rare",
        name="Silicon Carbide Spire",
        sector="Depth 36 // Deep Trench Furnace",
        description="An ultra-hard crystal spike forged in an industrial arc meltdown. Scratching it against steel blunts the metal instantly.",
    ),

    # =========================================================================
    # HOLO RARES (15 cards: S1-H-000 to S1-H-014)
    # =========================================================================
    Card(
        card_id="S1-H-000",
        tier="holo_rare",
        name="Phosphor Lattice Matrix",
        sector="Depth 22 // Optical Switching Yard",
        description="Pure crystalline substrate grown over a high-density logic array. Emits an uninterrupted 520nm green pulse across the dark fiber lines.",
    ),
    Card(
        card_id="S1-H-001",
        tier="holo_rare",
        name="Superconductor Bloom",
        sector="Depth 25 // Liquid Chiller Return",
        description="Ceramic cuprate ribbons that crystallized without resistance. Zero ohmic loss across a spiderweb of frozen signal traces.",
    ),
    Card(
        card_id="S1-H-002",
        tier="holo_rare",
        name="Amber Clock Beacon",
        sector="Depth 18 // Relay Tower Base",
        description="A high-tension timing quartz encased in warm resin. Illuminates the surrounding dust in steady 1-second pulse intervals.",
    ),
    Card(
        card_id="S1-H-003",
        tier="holo_rare",
        name="Prism Bus Cluster",
        sector="Depth 27 // Core Fiber Plenum",
        description="A dense bundle of photonic interconnects fused by excessive throughput. Light enters white and exits split across twelve distinct bands.",
    ),
    Card(
        card_id="S1-H-004",
        tier="holo_rare",
        name="Diffraction Monolith",
        sector="Depth 30 // Primary Gate Chamber",
        description="A polished slab of wafer-grade silicon etched with microscopic circuit pathways that gleam iridescent under white light.",
    ),
    Card(
        card_id="S1-H-005",
        tier="holo_rare",
        name="Cryo-Vein Prism",
        sector="Depth 29 // Cold Row Sub-Array",
        description="Coolant runoff crystallized into a jagged, superconducting prism. Terminal probes show a dead freeze upon contact.",
    ),
    Card(
        card_id="S1-H-006",
        tier="holo_rare",
        name="Laser-Etched Node Heart",
        sector="Depth 21 // Automation Hall",
        description="A die-stamped processing unit surrounded by pristine quartz filigree. The lithography lines glow faintly when energized.",
    ),
    Card(
        card_id="S1-H-007",
        tier="holo_rare",
        name="Optic Waveguide Spiral",
        sector="Depth 24 // Signal Routing Spiral",
        description="Spun silica glass twisted into an unbroken helix that feeds endless laser reflections back into itself.",
    ),
    Card(
        card_id="S1-H-008",
        tier="holo_rare",
        name="Pure Gold Die Lead",
        sector="Depth 16 // Assembly Cleanroom Remains",
        description="Microscopic bonding wires pulled into gleaming, uncorroded golden needles that never collected an ounce of dust.",
    ),
    Card(
        card_id="S1-H-009",
        tier="holo_rare",
        name="Thermal Shunt Crystal",
        sector="Depth 28 // Main Exhaust Stack",
        description="Formed in an exhaust channel where temperatures exceeded limits for months on end. Sucks heat directly out of the surrounding air.",
    ),
    Card(
        card_id="S1-H-010",
        tier="holo_rare",
        name="Static Ion Filament",
        sector="Depth 20 // High-Voltage Sub-Floor",
        description="Suspended between two rusted busbars, this gossamer strand hums with static tension that raises arm hair on approach.",
    ),
    Card(
        card_id="S1-H-011",
        tier="holo_rare",
        name="Refracted Clock Seed",
        sector="Depth 31 // Central Frequency Plenum",
        description="A synthetic quartz oscillator that calcified under extreme clock tension. Ticks with absolute zero drift against network UTC.",
    ),
    Card(
        card_id="S1-H-012",
        tier="holo_rare",
        name="Ferrite Core Toroid",
        sector="Depth 19 // Legacy Memory Bank",
        description="Hand-woven magnetic rings preserved in clear sealant. Holds the faint ghost impression of a decades-old block state.",
    ),
    Card(
        card_id="S1-H-013",
        tier="holo_rare",
        name="Phosphor Flare Shard",
        sector="Depth 23 // Display Assembly Wing",
        description="Curved CRT monitor glass impregnated with rare-earth phosphors that flare bright emerald when struck by stray electrons.",
    ),
    Card(
        card_id="S1-H-014",
        tier="holo_rare",
        name="Bifrost Conduit Splitter",
        sector="Depth 32 // Cross-District Trunk",
        description="A heavy junction box filled with prism glass that routes high-bandwidth optical carriers to three divergent subnet branches.",
    ),

    # =========================================================================
    # UNCOMMONS (40 cards: S1-U-000 to S1-U-039)
    # =========================================================================
    Card(
        card_id="S1-U-000",
        tier="uncommon",
        name="Amber Trace Shard",
        sector="Depth 03 // Conduit Trench",
        description="Fossilized resin and copper wire fused during the '84 grid blowout. Still bleeds a persistent 3-volt charge across the pins.",
    ),
    Card(
        card_id="S1-U-001",
        tier="uncommon",
        name="Burned Busbar Fragment",
        sector="Depth 05 // Transformer Yard",
        description="Thick blackened copper with iridescent heat tempering along its fractured edge.",
    ),
    Card(
        card_id="S1-U-002",
        tier="uncommon",
        name="Quartz Timing Crystal",
        sector="Depth 02 // Cable Vault Feeder",
        description="A tiny tuning fork sealed inside a weathered metal can. Rattles faintly when shaken.",
    ),
    Card(
        card_id="S1-U-003",
        tier="uncommon",
        name="Raw Silicate Gravel",
        sector="Depth 07 // Sub-Bed Foundation",
        description="Crushed semiconductor tailings discarded in the early days of the excavation.",
    ),
    Card(
        card_id="S1-U-004",
        tier="uncommon",
        name="Solder Bead Clust",
        sector="Depth 04 // Hand-Assembly Bay",
        description="A cluster of solidified lead-tin droplets collected from underneath an old rework bench.",
    ),
    Card(
        card_id="S1-U-005",
        tier="uncommon",
        name="Etched Ribbon Strand",
        sector="Depth 06 // Riser Shaft B",
        description="A flat 40-conductor cable whose brittle insulation has cracked to expose raw gray conductor pathways.",
    ),
    Card(
        card_id="S1-U-006",
        tier="uncommon",
        name="Corroded Heatsink Fin",
        sector="Depth 08 // Burned Sub-Floor",
        description="Extruded aluminum caked in dry thermal grease and black carbon dust.",
    ),
    Card(
        card_id="S1-U-007",
        tier="uncommon",
        name="Fiber Cladding Splinter",
        sector="Depth 09 // Inter-Rack Conduit",
        description="A protective glass jacket chipped away from an overhead optical feeder line.",
    ),
    Card(
        card_id="S1-U-008",
        tier="uncommon",
        name="Melted Resistor Array",
        sector="Depth 05 // Regulator Bay",
        description="Ceramic bricks whose color codes were baked into a uniform matte charcoal.",
    ),
    Card(
        card_id="S1-U-009",
        tier="uncommon",
        name="Grounding Braid Section",
        sector="Depth 01 // Surface Penetration",
        description="Flattened woven copper strap that carried lighting surges into the earth bedrock.",
    ),
    Card(
        card_id="S1-U-010",
        tier="uncommon",
        name="Polished Silicon Flake",
        sector="Depth 10 // Scrap Sorting Basin",
        description="A razor-sharp mirror chip of unetched monocrystalline ingot.",
    ),
    Card(
        card_id="S1-U-011",
        tier="uncommon",
        name="Tantalum Cap Shuck",
        sector="Depth 04 // Logic Shelf A",
        description="Yellow epoxy casing blown open from reverse-polarity failure decades ago.",
    ),
    Card(
        card_id="S1-U-012",
        tier="uncommon",
        name="Wire-Wrap Spindle",
        sector="Depth 03 // Backplane Trench",
        description="A hand-wired wire-wrap pin assembly resembling a miniature copper bird nest.",
    ),
    Card(
        card_id="S1-U-013",
        tier="uncommon",
        name="Slagged Die Matrix",
        sector="Depth 08 // Burned Sub-Floor",
        description="A silicon chip melted into surrounding silicate sandstone. Traces form an accidental green diffraction grating.",
    ),
    Card(
        card_id="S1-U-014",
        tier="uncommon",
        name="Optical Coupler Bead",
        sector="Depth 11 // Isolation Riser",
        description="Tiny glass prism used to jump control signals over high-voltage barriers.",
    ),
    Card(
        card_id="S1-U-015",
        tier="uncommon",
        name="Carbon Film Shard",
        sector="Depth 06 // Resistor Trench",
        description="Flaked resistive coating scraped off an oversized industrial attenuator plate.",
    ),
    Card(
        card_id="S1-U-016",
        tier="uncommon",
        name="Oxidized Pin Header",
        sector="Depth 02 // Junction Point 4",
        description="Dual-row gold pins covered in green copper carbonate patina.",
    ),
    Card(
        card_id="S1-U-017",
        tier="uncommon",
        name="Vitreous Slag Lump",
        sector="Depth 12 // Smelter Drainage",
        description="Glassy byproduct of an uncontrolled power-room fire, speckled with unburned tin nodules.",
    ),
    Card(
        card_id="S1-U-018",
        tier="uncommon",
        name="Ferrite Bead Choke",
        sector="Depth 07 // Filter Bank",
        description="Dull black magnetic cylinder salvaged from a high-frequency power feed.",
    ),
    Card(
        card_id="S1-U-019",
        tier="uncommon",
        name="Bleeder Resistor Core",
        sector="Depth 13 // High-Voltage Vault",
        description="Heavy ceramic tube wound with nichrome wire, designed to slowly drain dead capacitors.",
    ),
    Card(
        card_id="S1-U-020",
        tier="uncommon",
        name="Fused Relay Finger",
        sector="Depth 05 // Main Breaker Wall",
        description="Silver-plated contact welded together during a catastrophic short circuit.",
    ),
    Card(
        card_id="S1-U-021",
        tier="uncommon",
        name="Silica Gel Desiccant Pod",
        sector="Depth 01 // Sealed Enclosure 8",
        description="Tiny blue and amber moisture beads long since turned opaque and brittle.",
    ),
    Card(
        card_id="S1-U-022",
        tier="uncommon",
        name="Mica Insulator Sheet",
        sector="Depth 09 // Power Rack Mount",
        description="Thin, peelable mineral sheet used to keep transistors from grounding against the chassis.",
    ),
    Card(
        card_id="S1-U-023",
        tier="uncommon",
        name="Tin Whisker Cluster",
        sector="Depth 14 // Unchecked Sub-Rack",
        description="Microscopic metallic filaments that grew quietly across circuit traces until everything shorted.",
    ),
    Card(
        card_id="S1-U-024",
        tier="uncommon",
        name="Cracked Ceramic Substrate",
        sector="Depth 10 // Hybrid Module Hall",
        description="White alumina plate bearing screen-printed platinum-gold conductors.",
    ),
    Card(
        card_id="S1-U-025",
        tier="uncommon",
        name="Burned Toroid Core",
        sector="Depth 11 // Inductor Bench",
        description="Iron-powder ring cracked clean in half from extreme magnetic saturation.",
    ),
    Card(
        card_id="S1-U-026",
        tier="uncommon",
        name="Raw Silicate Core",
        sector="Depth 14 // Trench Feeder 6",
        description="Metamorphic silicon pulled from cooling runoff. Heavy and cool despite ambient floor heat.",
    ),
    Card(
        card_id="S1-U-027",
        tier="uncommon",
        name="Gold Finger Scraps",
        sector="Depth 03 // PCIe Graveyard",
        description="Beveled card-edge connectors sheared off junked expansion modules.",
    ),
    Card(
        card_id="S1-U-028",
        tier="uncommon",
        name="Dielectric Glass Spacer",
        sector="Depth 15 // Capacitor Bank Sub-Floor",
        description="Precision glass pane used to separate massive high-potential storage plates.",
    ),
    Card(
        card_id="S1-U-029",
        tier="uncommon",
        name="Nichrome Heating Loop",
        sector="Depth 08 // Thermal Chamber",
        description="A twisted heating coil that ran red-hot in an environmental stress test oven.",
    ),
    Card(
        card_id="S1-U-030",
        tier="uncommon",
        name="Inductor Core Slug",
        sector="Depth 12 // Filter Array B",
        description="Threaded ferrite slug once used to manually calibrate antenna tuning coils.",
    ),
    Card(
        card_id="S1-U-031",
        tier="uncommon",
        name="Thermal Paste Crust",
        sector="Depth 06 // CPU Socket Scrap",
        description="Silver-gray chalky residue baked hard between chip lids and heatsink bases.",
    ),
    Card(
        card_id="S1-U-032",
        tier="uncommon",
        name="Copper Slug Extrusion",
        sector="Depth 13 // Vapor Chamber Runoff",
        description="Flattened copper heatpipe segment ruptured by internal steam pressure.",
    ),
    Card(
        card_id="S1-U-033",
        tier="uncommon",
        name="Leadframe Shingle",
        sector="Depth 04 // Packaging Line Waste",
        description="Uncut brass carrier frame stamped for surface-mount ICs before plastic encapsulation.",
    ),
    Card(
        card_id="S1-U-034",
        tier="uncommon",
        name="PZT Piezo Disk",
        sector="Depth 02 // Alarm Siren Enclosure",
        description="Thin brass disk backed with piezoelectric ceramic that clicked whenever voltage jumped.",
    ),
    Card(
        card_id="S1-U-035",
        tier="uncommon",
        name="Boric Glass Lens",
        sector="Depth 15 // Optical Transceiver Yard",
        description="Heat-resistant focusing element recovered from an infrared laser transceiver head.",
    ),
    Card(
        card_id="S1-U-036",
        tier="uncommon",
        name="Silicon Scribe Chip",
        sector="Depth 10 // Dicing Saw Pit",
        description="Diamond saw slurry pressed into a cake with microscopic raw wafer fragments.",
    ),
    Card(
        card_id="S1-U-037",
        tier="uncommon",
        name="Stray Bumper Resistor",
        sector="Depth 07 // Floor Drain Filter",
        description="A tiny surface-mount resistor washed into a sub-floor drainage grating.",
    ),
    Card(
        card_id="S1-U-038",
        tier="uncommon",
        name="Transformer Bobbin Flange",
        sector="Depth 09 // Inverter Room",
        description="Phenolic plastic coil former scorched along the primary winding boundary.",
    ),
    Card(
        card_id="S1-U-039",
        tier="uncommon",
        name="Lithography Alignment Mark",
        sector="Depth 16 // Stepper Chamber Remains",
        description="A crosshair pattern etched onto silicon edge trim to help the stepper align exposures.",
    ),
]

CARD_SETS: Dict[str, List[Card]] = {
    "secret_rare": [c for c in SET_1_CARDS if c.tier == "secret_rare"],
    "holo_rare": [c for c in SET_1_CARDS if c.tier == "holo_rare"],
    "uncommon": [c for c in SET_1_CARDS if c.tier == "uncommon"],
}

CARDS_BY_ID: Dict[str, Card] = {c.card_id: c for c in SET_1_CARDS}


def get_card(card_id: str) -> Optional[Card]:
    return CARDS_BY_ID.get(card_id)


def list_cards(tier: Optional[str] = None) -> List[Card]:
    if tier is None:
        return list(SET_1_CARDS)
    return CARD_SETS.get(tier, [])


def pool_size(tier: str) -> int:
    return len(CARD_SETS.get(tier, []))
SET_ID = "S1"
