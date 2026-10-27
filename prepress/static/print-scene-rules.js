/*
 * What a printed banner, mesh or rigid board stands on, and how it is finished, as maths: pure, so Node tests it
 * (tests/print_scene_rules.test.js); scene.html builds the three.js scene from it. Millimetres in, millimetres out.
 *
 * The carrier follows the ORDERED SIZE (the shop, 2026-10-27): a banner low enough for a crowd-control barrier
 * (barierka, the event barrier the shop's reference shows — a3flags.com/event-barrier-fence-branding) hangs on a row
 * of them, one up to a mobile fence panel's height on Heras panels, one taller on a billboard frame, and one bigger
 * than the largest billboard on a free-standing truss frame. Wider than one carrier spans several. The shop sells the banner only — the customer mounts it —
 * so the carrier is scenery, never a product.
 */
( function ( root ) {
	// A crowd-control barrier: 2 500 × 1 100 mm, Ø38 mm frame with rounded top corners, Ø20 mm infill bars at 125 mm,
	// splayed legs at each end; the banner covers the infill, at most `bannerHeight` tall.
	const BARRIER = { width: 2500, height: 1100, tube: 38, bar: 20, barPitch: 125, corner: 150, legSpread: 600,
	                  bannerHeight: 1000 };
	// A Heras M500 mobile fence panel: 3 500 × 2 000 mm, Ø42 mm tube frame, 100 × 300 mm wire mesh of Ø4 mm wire,
	// standing in a 600 × 220 × 140 mm concrete foot.
	const FENCE = { width: 3500, height: 2000, tube: 42, wire: 4, cellWidth: 100, cellHeight: 300, foot: [ 600, 220, 140 ] };
	// The largest billboard the scene puts a banner on; bigger goes on a frame (the shop: "like 7x5m i think").
	const BILLBOARD = { width: 7000, height: 5000, clearance: 1500 };
	const ROLL_WIDTH = 3200;    // the widest roll: a wider banner is welded from two or more parts
	const EYELET_PITCH = 500;   // the standard finishing: eyelets every 50 cm round every edge
	const EYELET_INSET = 25;    // from the edge to the eyelet's centre, inside the welded hem

	/** Where a banner `width` × `height` mm hangs: { kind: 'barrier' | 'fence', panels } | { kind: 'billboard' } |
	 * { kind: 'frame' }. */
	function carrierFor( width, height ) {
		if ( height <= BARRIER.bannerHeight ) {
			return { kind: 'barrier', panels: Math.max( 1, Math.ceil( width / BARRIER.width ) ) };
		}
		if ( height <= FENCE.height ) {
			return { kind: 'fence', panels: Math.max( 1, Math.ceil( width / FENCE.width ) ) };
		}
		if ( width <= BILLBOARD.width && height <= BILLBOARD.height ) {
			return { kind: 'billboard' };
		}
		return { kind: 'frame' };
	}

	/** Eyelet centres [x, y] from the banner's bottom-left corner, round all four edges: every corner has one and no
	 * two are further apart than `pitch` (the spacing is evened out between the corners, as the finisher does). */
	function eyelets( width, height, pitch = EYELET_PITCH ) {
		const inset = Math.min( EYELET_INSET, width / 4, height / 4 );
		const stops = ( length ) => {
			const span = length - 2 * inset;
			const count = Math.max( 1, Math.ceil( span / pitch ) );
			return Array.from( { length: count + 1 }, ( _, i ) => inset + ( span * i ) / count );
		};
		const xs = stops( width );
		const ys = stops( height );
		const found = [];
		const seen = new Set();
		const add = ( x, y ) => {
			const key = `${ x.toFixed( 3 ) }:${ y.toFixed( 3 ) }`;
			if ( ! seen.has( key ) ) {
				seen.add( key );
				found.push( [ x, y ] );
			}
		};
		xs.forEach( ( x ) => { add( x, inset ); add( x, height - inset ); } );
		ys.forEach( ( y ) => { add( inset, y ); add( width - inset, y ); } );
		return found;
	}

	/** The welded seams' x positions (from the left) in a banner wider than one roll; none when it fits a roll. */
	function seams( width, roll = ROLL_WIDTH ) {
		const found = [];
		for ( let x = roll; x < width - 1; x += roll ) {
			found.push( x );
		}
		return found;
	}

	/**
	 * Which scene a price-list material name asks for: { kind: 'baner' | 'siatka' | 'plyta', thickness, sides } or
	 * null for a material no scene shows (a film, a wallpaper, a fabric). A rigid board carries its thickness in its
	 * name („PCV Spienione 5mm", „Folia magnetyczna 0,7 mm" is a film, not a board); „Dwustronn…" is both faces printed.
	 */
	function sceneOf( materialName ) {
		const name = String( materialName || '' );
		const sides = /dwustronn/i.test( name ) ? 2 : 1;
		if ( /flaga|folia|tapeta|poliuretan|tpu|papier|tkanina|decor|print(floor|mat)|tektura|plaster/i.test( name ) ) {
			return null;
		}
		if ( /siatka|mesh/i.test( name ) ) {
			return { kind: 'siatka', thickness: 0, sides: 1 };
		}
		if ( /baner|frontlit|blockout/i.test( name ) ) {
			return { kind: 'baner', thickness: 0, sides: 1 };
		}
		const thickness = name.match( /(\d+(?:[.,]\d+)?)\s*mm/i );
		if ( thickness ) {
			return { kind: 'plyta', thickness: Number( thickness[ 1 ].replace( ',', '.' ) ), sides };
		}
		return null;
	}

	const api = { BARRIER, FENCE, BILLBOARD, ROLL_WIDTH, EYELET_PITCH, carrierFor, eyelets, seams, sceneOf };
	if ( typeof module !== 'undefined' && module.exports ) {
		module.exports = api;
	} else {
		root.PrepressPrintScene = api;
	}
} )( typeof window !== 'undefined' ? window : globalThis );
