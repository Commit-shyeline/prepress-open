// node tests/shot_frame.test.js — the `?shot=1` data-shot maths (prepress/static/shot-frame.js), on a fake
// camera that projects x and y linearly, so the pixels can be worked out by hand.
const assert = require( 'assert' );

class Vector {
	constructor( x, y, z ) { this.x = x; this.y = y; this.z = z; }
	clone() { return new Vector( this.x, this.y, this.z ); }
	setY( y ) { this.y = y; return this; }
	project( camera ) { return new Vector( ( this.x - camera.cx ) / camera.hw, ( this.y - camera.cy ) / camera.hh, 0 ); }
}

( async () => {
	const { shotData } = await import( '../prepress/static/shot-frame.js' );
	// a camera seeing x in [−2, 2] and y in [−1, 3] across an 800 × 600 picture; the product's foot at (−1, 0)
	const camera = { cx: 0, hw: 2, cy: 1, hh: 2, updated: 0, updateMatrixWorld() { this.updated++; } };
	const shot = JSON.parse( shotData( camera, new Vector( -1, 0, 0 ), 1, 800, 600, 2.5 ) );
	assert.deepStrictEqual( shot, { left: 200, ground: 450, pxPerMetre: 150, heightM: 2.5 } );
	assert.strictEqual( camera.updated, 1, 'the matrix is refreshed before projecting — only a render would otherwise' );
	// flag units: a metre is 1000 scene units, and the height arrives in metres already
	const flag = JSON.parse( shotData( camera, new Vector( -1, 0, 0 ), 1000, 800, 600, 1.234567 ) );
	assert.strictEqual( flag.heightM, 1.235 );
	assert.strictEqual( flag.pxPerMetre, 150000, 'a metre of 1000 units: (1000 − 1) / 2 of the frame, 150 px each' );
	console.log( 'OK — shot frame: ground line, left edge, pixels per metre' );
} )().catch( ( error ) => { console.error( error ); process.exit( 1 ); } );
