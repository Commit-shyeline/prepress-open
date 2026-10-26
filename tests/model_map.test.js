// node tests/model_map.test.js — the 3D model ↔ template page maths (prepress/static/model-map.js).
const assert = require( 'assert' );

const map = require( '../prepress/static/model-map.js' );

const close = ( a, b, tolerance = 1e-9 ) => assert.ok( Math.abs( a - b ) <= tolerance, `${ a } ≠ ${ b }` );

// ── islands: two quads that share no vertex are two faces; a quad is one ──
{
	// quad A: vertices 0-3, quad B: vertices 4-7, each two triangles
	const index = [ 0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7 ];
	const found = map.islands( index, 8 );
	assert.strictEqual( found.count, 2 );
	assert.deepStrictEqual( Array.from( found.ofTriangle ), [ 0, 0, 1, 1 ] );
	// numbered by first appearance: a mesh listing B's triangles first calls B island 0
	assert.deepStrictEqual( Array.from( map.islands( [ 4, 5, 6, 0, 1, 2 ], 8 ).ofTriangle ), [ 0, 1 ] );
}

// ── fitAffine recovers a known 2D affine exactly from three pairs ──
{
	// page = 0.5 * u + 0.1, page_v = -0.25 * v + 0.9 (a mirrored, shrunk, shifted island)
	const truth = [ [ 0.5, 0, 0.1 ], [ 0, - 0.25, 0.9 ] ];
	const pairs = [ [ 0, 0 ], [ 1, 0 ], [ 0, 1 ] ].map( ( from ) => ( { from, to: map.applyAffine( truth, from ) } ) );
	const fit = map.fitAffine( pairs );
	assert.ok( fit );
	fit.affine.forEach( ( row, r ) => row.forEach( ( value, c ) => close( value, truth[ r ][ c ] ) ) );
	close( fit.residual[ 0 ], 0 ); close( fit.residual[ 1 ], 0 );
}

// ── least squares over five noisy pairs lands within the noise, and reports the worst miss ──
{
	const truth = [ [ 0.4, 0.1, 0.2 ], [ - 0.1, 0.5, 0.3 ] ];
	const noise = [ 0.004, - 0.003, 0.002, - 0.004, 0.001 ];
	const pairs = [ [ 0, 0 ], [ 1, 0 ], [ 0, 1 ], [ 1, 1 ], [ 0.5, 0.5 ] ].map( ( from, i ) => {
		const to = map.applyAffine( truth, from );
		return { from, to: [ to[ 0 ] + noise[ i ], to[ 1 ] - noise[ i ] ] };
	} );
	const fit = map.fitAffine( pairs );
	fit.affine.forEach( ( row, r ) => row.forEach( ( value, c ) => close( value, truth[ r ][ c ], 0.02 ) ) );
	assert.ok( fit.residual[ 0 ] > 0 && fit.residual[ 0 ] < 0.01 );
}

// ── too few pairs, or pairs on one line, are refused rather than guessed ──
{
	assert.strictEqual( map.fitAffine( [] ), null );
	assert.strictEqual( map.fitAffine( [ { from: [ 0, 0 ], to: [ 0, 0 ] }, { from: [ 1, 1 ], to: [ 1, 1 ] } ] ), null );
	const collinear = [ [ 0, 0 ], [ 1, 1 ], [ 2, 2 ] ].map( ( from ) => ( { from, to: [ from[ 0 ] / 2, 0 ] } ) );
	assert.strictEqual( map.fitAffine( collinear ), null );
}

// ── a mesh without UVs: a tilted flat panel is projected from its local XYZ with four pairs ──
{
	// the panel's corners in 3D, and where each lies on the page
	const corners = [ [ 0, 0, 0 ], [ 2, 0, 1 ], [ 0, 1.5, 0.5 ], [ 2, 1.5, 1.5 ] ];
	const page = [ [ 0.1, 0.9 ], [ 0.9, 0.9 ], [ 0.1, 0.1 ], [ 0.9, 0.1 ] ];
	const fit = map.fitAffine( corners.map( ( from, i ) => ( { from, to: page[ i ] } ) ) );
	assert.ok( fit );
	const middle = map.applyAffine( fit.affine, [ 1, 0.75, 0.75 ] );
	close( middle[ 0 ], 0.5, 1e-6 ); close( middle[ 1 ], 0.5, 1e-6 );
	// a point lifted off the panel lands where its foot on the panel does: the fit is a projection
	// the panel's normal is (2,0,1) × (0,1.5,0.5) = (-1.5, -1, 3), length 3.5
	const lifted = map.applyAffine( fit.affine, [ 1 - 1.5 / 3.5, 0.75 - 1 / 3.5, 0.75 + 3 / 3.5 ] );
	close( lifted[ 0 ], 0.5, 1e-5 ); close( lifted[ 1 ], 0.5, 1e-5 );
	// four points on one LINE in space are refused: no plane to project onto
	const line = [ 0, 1, 2, 3 ].map( ( k ) => ( { from: [ k, k, 0 ], to: [ k / 3, 0.5 ] } ) );
	assert.strictEqual( map.fitAffine( line ), null );
}

// ── remap writes page positions for one island only; regroup puts the printed triangles first ──
{
	const index = [ 0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7 ];
	const uv = [ 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1 ];
	const { ofTriangle } = map.islands( index, 8 );
	const target = new Array( 16 ).fill( - 1 );
	const affine = [ [ 0.5, 0, 0.25 ], [ 0, 0.5, 0.25 ] ];
	const written = map.remap( { source: uv, size: 2, index, ofTriangle, island: 1, affine, target } );
	assert.strictEqual( written, 4 );
	assert.deepStrictEqual( target.slice( 0, 8 ), new Array( 8 ).fill( - 1 ) );      // island 0 untouched
	assert.deepStrictEqual( target.slice( 8 ), [ 0.25, 0.25, 0.75, 0.25, 0.75, 0.75, 0.25, 0.75 ] );
	const grouped = map.regroup( index, ofTriangle, new Set( [ 1 ] ) );
	assert.strictEqual( grouped.mappedTriangles, 2 );
	assert.deepStrictEqual( grouped.index, [ 4, 5, 6, 4, 6, 7, 0, 1, 2, 0, 2, 3 ] );
	assert.deepStrictEqual( grouped.order, [ 2, 3, 0, 1 ] );
}

// ── the automatic pass: an island's box is laid on the page box its aspect fits, upright when it can be ──
{
	const index = [ 0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7 ];
	const uv = [ 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 0.1, 0, 0.1, 0.1, 0, 0.1 ];
	const { ofTriangle } = map.islands( index, 8 );
	const box0 = map.islandBox( { source: uv, size: 2, index, ofTriangle, island: 0 } );
	assert.deepStrictEqual( box0, [ 0, 0, 1, 1 ] );
	assert.deepStrictEqual( map.islandBox( { source: uv, size: 2, index, ofTriangle, island: 1 } ), [ 0, 0, 0.1, 0.1 ] );
	assert.strictEqual( map.islandBox( { source: uv, size: 2, index, ofTriangle, island: 7 } ), null );

	// a model whose UVs already ARE the page (a Blender-built one): the identity, upright, not mirrored
	const identity = map.boxFit( [ 0, 0, 1, 1 ], [ 0, 0, 1, 1 ] );
	assert.strictEqual( identity.turn, 0 ); assert.strictEqual( identity.mirror, false );
	identity.affine.forEach( ( row, r ) => row.forEach( ( value, c ) => close( value, [ [ 1, 0, 0 ], [ 0, 1, 0 ] ][ r ][ c ] ) ) );

	// a tall island on a wide page box turns 90°; the island's top-left corner lands on the box's top-right
	const turned = map.boxFit( [ 0, 0, 1, 2 ], [ 0.1, 0.1, 0.9, 0.5 ] );
	assert.strictEqual( turned.turn, 1 ); assert.strictEqual( turned.mirror, false );
	const topLeft = map.applyAffine( turned.affine, [ 0, 0 ] );
	close( topLeft[ 0 ], 0.9 ); close( topLeft[ 1 ], 0.1 );
	const bottomLeft = map.applyAffine( turned.affine, [ 0, 2 ] );
	close( bottomLeft[ 0 ], 0.1 ); close( bottomLeft[ 1 ], 0.1 );

	// two islands, two page boxes: the big one takes the big box, each box used once
	const fits = map.autoFit( [ { key: 'a', box: [ 0, 0, 0.1, 0.1 ] }, { key: 'b', box: [ 0, 0, 1, 1 ] } ],
		[ [ 0.05, 0.05, 0.25, 0.25 ], [ 0.3, 0.1, 0.9, 0.7 ] ] );
	assert.deepStrictEqual( fits.map( ( fit ) => [ fit.key, fit.pageBox ] ), [ [ 'b', 1 ], [ 'a', 0 ] ] );
	// one page box for two islands: both wear it
	const shared = map.autoFit( [ { key: 'a', box: [ 0, 0, 1, 1 ] }, { key: 'b', box: [ 0, 0, 1, 1 ] } ], [ [ 0, 0, 1, 1 ] ] );
	assert.deepStrictEqual( shared.map( ( fit ) => fit.pageBox ), [ 0, 0 ] );
	assert.deepStrictEqual( map.autoFit( [ { key: 'a', box: [ 0, 0, 1, 1 ] } ], [] ), [] );
}

console.log( 'model_map: ok' );
