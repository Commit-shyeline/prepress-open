// node tests/designer_model.test.js — the design editor's geometry (prepress/static/designer-model.js).
// tests/test_designer_model.py runs this under pytest where Node is installed.
const assert = require( 'node:assert/strict' );
const model = require( '../prepress/static/designer-model.js' );

const close = ( actual, expected, message ) => assert.ok( Math.abs( actual - expected ) < 1e-6, `${ message }: ${ actual } vs ${ expected }` );
const logo = { id: 'a', type: 'image', src: 'data:,', x: 500, y: 400, scale: 0.5, rotation: 0, width: 400, height: 200 };

// An unrotated picture 200 × 100 at scale 0.5, centred on (500, 400): its box and its corners
assert.deepEqual( model.bounds( logo ), { left: 400, top: 350, right: 600, bottom: 450 } );
assert.deepEqual( model.corners( logo )[ 0 ], { x: 400, y: 350 } );

// a point inside, a point outside, and the same picture turned a quarter: the box turns with it
assert.equal( model.contains( logo, { x: 590, y: 440 } ), true );
assert.equal( model.contains( logo, { x: 610, y: 440 } ), false );
const turned = { ...logo, rotation: Math.PI / 2 };
assert.equal( model.contains( turned, { x: 540, y: 490 } ), true ); // now 100 wide and 200 tall
assert.equal( model.contains( turned, { x: 590, y: 440 } ), false );
const box = model.bounds( turned );
close( box.left, 450, 'turned left' );
close( box.top, 300, 'turned top' );

// the topmost of two overlapping elements is the one drawn last
const text = { id: 'b', type: 'text', text: 'LUXPOL', colour: '#000', size: 80, x: 560, y: 420, scale: 1, rotation: 0, width: 300, height: 96 };
assert.equal( model.hitTest( [ logo, text ], { x: 560, y: 420 } ).id, 'b' );
assert.equal( model.hitTest( [ text, logo ], { x: 560, y: 420 } ).id, 'a' );
assert.equal( model.hitTest( [ logo, text ], { x: 10, y: 10 } ), null );

// handles: the scale handle sits on the bottom-right corner, the rotate handle above the top edge; grabbing within reach
const at = model.handles( logo );
assert.deepEqual( at.scale, { x: 600, y: 450 } );
close( at.rotate.x, 500, 'rotate handle x' );
close( at.rotate.y, 350 - model.ROTATE_HANDLE_OFFSET, 'rotate handle y' );
assert.equal( model.handleAt( logo, { x: 605, y: 455 } ), 'scale' );
assert.equal( model.handleAt( logo, { x: 500, y: 350 - model.ROTATE_HANDLE_OFFSET + 5 } ), 'rotate' );
assert.equal( model.handleAt( logo, { x: 500, y: 400 } ), null );
assert.equal( model.handleAt( logo, { x: 605, y: 455 }, 4 ), null ); // zoomed in 4×, the handle's reach on the canvas is a quarter

// drags: a move shifts, a scale drag away from the centre grows (and never below the minimum), a rotate drag sweeps the angle
assert.deepEqual( model.dragged( logo, 'move', { x: 0, y: 0 }, { x: 30, y: -20 } ), { ...logo, x: 530, y: 380 } );
close( model.dragged( logo, 'scale', { x: 600, y: 450 }, { x: 700, y: 500 } ).scale, 1, 'scale doubled' );
close( model.dragged( logo, 'scale', { x: 600, y: 450 }, { x: 500, y: 400 } ).scale, 0.05, 'scale floor' );
close( model.dragged( logo, 'rotate', { x: 600, y: 400 }, { x: 500, y: 500 } ).rotation, Math.PI / 2, 'quarter turn' );
assert.deepEqual( model.dragged( logo, 'nonsense', { x: 0, y: 0 }, { x: 1, y: 1 } ), logo );

// the centre stays on the canvas
assert.deepEqual( model.clamped( { ...logo, x: -40, y: 5000 }, 2048, 1024 ), { ...logo, x: 0, y: 1024 } );

// list edits
const both = [ logo, text ];
assert.deepEqual( model.raise( both, 'a' ).map( ( each ) => each.id ), [ 'b', 'a' ] );
assert.deepEqual( model.remove( both, 'a' ).map( ( each ) => each.id ), [ 'b' ] );
assert.equal( model.replace( both, { ...logo, x: 1 } )[ 0 ].x, 1 );

// new elements land in the middle; a picture covers about a third of the canvas
const picture = model.newImage( 'data:,', 3000, 1000, 2048, 1024 );
assert.deepEqual( [ picture.x, picture.y ], [ 1024, 512 ] );
close( picture.scale, 2048 / 3 / 3000, 'a third of the width' );
assert.equal( model.newImage( 'data:,', 100, 50, 2048, 1024 ).scale, 1 ); // a small picture is not blown up
const word = model.newText( 'Hej', '#000', 120, 2048, 1024 );
assert.equal( word.type, 'text' );
assert.equal( word.height, 144 );

// Polish plurals and the summary's words
const words = { image: [ 'grafika', 'grafiki', 'grafik' ], text: [ 'napis', 'napisy', 'napisów' ] };
assert.equal( model.plural( 1, words.image ), 'grafika' );
assert.equal( model.plural( 3, words.image ), 'grafiki' );
assert.equal( model.plural( 5, words.image ), 'grafik' );
assert.equal( model.plural( 12, words.image ), 'grafik' );
assert.equal( model.plural( 22, words.image ), 'grafiki' );
assert.equal( model.summary( [ logo, logo, text ], words ), '2 grafiki, 1 napis' );
assert.equal( model.summary( [ text ], words ), '1 napis' );
assert.equal( model.summary( [], words ), '' );

console.log( 'designer_model: ok' );
