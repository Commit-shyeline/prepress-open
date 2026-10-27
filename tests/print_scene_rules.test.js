// node tests/print_scene_rules.test.js — what a printed banner stands on and how it is finished (prepress/static/print-scene-rules.js).
const assert = require( 'assert' );

const rules = require( '../prepress/static/print-scene-rules.js' );

// ── the carrier follows the ordered size: barrier → fence → billboard → frame ──
{
	assert.deepStrictEqual( rules.carrierFor( 2500, 1000 ), { kind: 'barrier', panels: 1 } ); // one barrier's cover
	assert.deepStrictEqual( rules.carrierFor( 3000, 1000 ), { kind: 'barrier', panels: 2 } );
	assert.deepStrictEqual( rules.carrierFor( 10000, 800 ), { kind: 'barrier', panels: 4 } );
	assert.deepStrictEqual( rules.carrierFor( 3400, 1800 ), { kind: 'fence', panels: 1 } ); // the standard Heras banner
	assert.deepStrictEqual( rules.carrierFor( 3500, 2000 ), { kind: 'fence', panels: 1 } ); // exactly one panel
	assert.deepStrictEqual( rules.carrierFor( 7000, 1500 ), { kind: 'fence', panels: 2 } );
	assert.deepStrictEqual( rules.carrierFor( 7100, 1500 ), { kind: 'fence', panels: 3 } );
	assert.deepStrictEqual( rules.carrierFor( 4000, 3000 ), { kind: 'billboard' } );   // taller than the fence
	assert.deepStrictEqual( rules.carrierFor( 7000, 5000 ), { kind: 'billboard' } );   // the largest billboard
	assert.deepStrictEqual( rules.carrierFor( 8000, 5000 ), { kind: 'frame' } );
	assert.deepStrictEqual( rules.carrierFor( 2000, 5100 ), { kind: 'frame' } );
}

// ── eyelets: every corner, never further apart than the pitch, evened out ──
{
	const found = rules.eyelets( 3000, 1000 );
	const corners = [ [ 25, 25 ], [ 2975, 25 ], [ 25, 975 ], [ 2975, 975 ] ];
	corners.forEach( ( corner ) => assert.ok( found.some( ( [ x, y ] ) => x === corner[ 0 ] && y === corner[ 1 ] ), `corner ${ corner }` ) );
	// 2 950 mm between the corner eyelets at a 500 pitch: 6 gaps of 491.67, so 7 per long edge, 3 per short edge
	assert.strictEqual( found.filter( ( [ , y ] ) => y === 25 ).length, 7 );
	assert.strictEqual( found.filter( ( [ x ] ) => x === 25 ).length, 3 );
	assert.strictEqual( found.length, 7 * 2 + 3 * 2 - 4 );                 // corners counted once
	// the denser add-on: 100 mm pitch on the same banner
	assert.strictEqual( rules.eyelets( 3000, 1000, 100 ).filter( ( [ , y ] ) => y === 25 ).length, 31 );
	// a tiny print keeps its eyelets inside it
	rules.eyelets( 60, 60 ).forEach( ( [ x, y ] ) => assert.ok( x >= 0 && x <= 60 && y >= 0 && y <= 60 ) );
}

// ── seams: a banner wider than the roll is welded from parts ──
{
	assert.deepStrictEqual( rules.seams( 3000 ), [] );
	assert.deepStrictEqual( rules.seams( 3200 ), [] );                    // exactly one roll
	assert.deepStrictEqual( rules.seams( 5000 ), [ 3200 ] );
	assert.deepStrictEqual( rules.seams( 7000 ), [ 3200, 6400 ] );
}

// ── the price-list material names → which scene ──
{
	assert.deepStrictEqual( rules.sceneOf( 'Baner powlekany Frontlit Black Back (450g)' ), { kind: 'baner', thickness: 0, sides: 1 } );
	assert.deepStrictEqual( rules.sceneOf( 'Siatka (baner mesh - 270g)' ), { kind: 'siatka', thickness: 0, sides: 1 } );
	assert.deepStrictEqual( rules.sceneOf( 'PCV Spienione 5mm' ), { kind: 'plyta', thickness: 5, sides: 1 } );
	assert.deepStrictEqual( rules.sceneOf( 'PCV Spienione Dwustronne 10mm' ), { kind: 'plyta', thickness: 10, sides: 2 } );
	assert.deepStrictEqual( rules.sceneOf( 'ACM (Tubond/Dibond) szczotkowany 3mm' ), { kind: 'plyta', thickness: 3, sides: 1 } );
	assert.deepStrictEqual( rules.sceneOf( 'Hips Dwustronny 1mm' ), { kind: 'plyta', thickness: 1, sides: 2 } );
	assert.strictEqual( rules.sceneOf( 'Folia magnetyczna 0,7 mm' ), null );   // a film, not a board
	assert.strictEqual( rules.sceneOf( 'Flaga mesh (120 g)' ), null );          // a flag has its own scene
	assert.strictEqual( rules.sceneOf( 'Tapeta flizelinowa 137cm' ), null );
	assert.strictEqual( rules.sceneOf( '' ), null );
}

console.log( 'print_scene_rules: ok' );
