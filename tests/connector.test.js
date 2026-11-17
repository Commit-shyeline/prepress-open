/*
 * The WordPress connector's hand-off reader (wordpress/prepress-open-connector/connector.js), run under Node
 * against a stand-in browser: the page's URL, its storage, its history and its events are the stubs below.
 * Run: node tests/connector.test.js   (tests/test_connector.py wraps it for pytest)
 */
const assert = require( 'node:assert/strict' );
const fs = require( 'node:fs' );
const path = require( 'node:path' );
const vm = require( 'node:vm' );

const SCRIPT = fs.readFileSync( path.join( __dirname, '..', 'wordpress', 'prepress-open-connector', 'connector.js' ), 'utf8' );
const INSTANCE = 'https://check.example/sprawdz';

/** A page at `url` with the connector configured for INSTANCE; returns what the script did to it. */
function openPage( url, { readyState = 'complete', instance = INSTANCE } = {} ) {
	const events = [];
	const stored = {};
	let replaced = null;
	const listeners = {};
	const location = new URL( url );
	const window = {
		PrepressOpenConnector: { instance, storageKey: 'prepress-open:design' },
		location: { search: location.search, pathname: location.pathname, hash: location.hash },
		sessionStorage: { setItem: ( key, value ) => { stored[ key ] = value; } },
		history: { state: null, replaceState: ( state, title, next ) => { replaced = next; } },
	};
	const document = {
		readyState,
		dispatchEvent: ( event ) => events.push( event ),
		addEventListener: ( name, handler ) => { listeners[ name ] = handler; },
	};
	class CustomEvent {
		constructor( type, init ) {
			this.type = type;
			this.detail = init.detail;
		}
	}
	vm.runInNewContext( SCRIPT, { window, document, CustomEvent, URLSearchParams, encodeURIComponent, String, RegExp, JSON, console } );
	return { events, stored, replaced, listeners };
}

// A customer back from the editor: the design named, the URL cleaned, the page told.
{
	const page = openPage( `https://shop.example/produkt/flaga/?kolor=zielony&design=B4DXNgo4&template=Wn5ikpfs&png=${ encodeURIComponent( INSTANCE + '/api/designs/B4DXNgo4/png' ) }#wycena` );
	assert.equal( page.events.length, 1 );
	assert.equal( page.events[ 0 ].type, 'prepress-open:design' );
	// The detail was made in the script's own context, so its prototype differs: compare it as plain data.
	assert.deepEqual( JSON.parse( JSON.stringify( page.events[ 0 ].detail ) ), {
		held: 'B4DXNgo4',
		template: 'Wn5ikpfs',
		png: INSTANCE + '/api/designs/B4DXNgo4/png',
		pdf: INSTANCE + '/api/designs/B4DXNgo4/pdf',
	} );
	assert.equal( page.replaced, '/produkt/flaga/?kolor=zielony#wycena', 'the shop\'s own query and hash stay, the design leaves the URL' );
	assert.deepEqual( JSON.parse( page.stored[ 'prepress-open:design' ] ), JSON.parse( JSON.stringify( page.events[ 0 ].detail ) ) );
}

// A picture from somewhere else is not passed on; the token still is.
{
	const page = openPage( 'https://shop.example/p/?design=B4DXNgo4&template=Wn5ikpfs&png=https%3A%2F%2Fevil.example%2Fx.png' );
	assert.equal( page.events[ 0 ].detail.png, '' );
	assert.equal( page.events[ 0 ].detail.held, 'B4DXNgo4' );
}

// Nothing to read: no event, no storage, the URL untouched.
{
	const page = openPage( 'https://shop.example/p/?kolor=zielony' );
	assert.equal( page.events.length, 0 );
	assert.equal( page.replaced, null );
	assert.deepEqual( page.stored, {} );
}

// A token that is not a token (the URL came from outside) is ignored whole.
{
	const page = openPage( 'https://shop.example/p/?design=%3Cscript%3E&template=Wn5ikpfs' );
	assert.equal( page.events.length, 0 );
}

// A page still loading hears it once the DOM is there.
{
	const page = openPage( 'https://shop.example/p/?design=B4DXNgo4&template=Wn5ikpfs', { readyState: 'loading' } );
	assert.equal( page.events.length, 0 );
	page.listeners.DOMContentLoaded();
	assert.equal( page.events.length, 1 );
}

/** A product page with one 3D frame and WooCommerce's jQuery; returns the frame and a way to pick a variation. */
function openProductPage( shown ) {
	const frame = { src: shown, getAttribute: () => frame.src, setAttribute: ( name, value ) => { frame.src = value; frame.loads += 1; }, loads: 0 };
	let onVariation = null;
	const document = {
		readyState: 'complete',
		querySelectorAll: ( selector ) => ( selector === 'iframe.prepress-open-model' ? [ frame ] : [] ),
	};
	const window = {
		PrepressOpenConnector: { instance: INSTANCE },
		location: { search: '', pathname: '/p/', hash: '' },
		jQuery: () => ( { on: ( name, handler ) => { if ( name === 'found_variation' ) onVariation = handler; } } ),
	};
	vm.runInNewContext( SCRIPT, { window, document, URLSearchParams, String, RegExp, JSON, console } );
	return { frame, pick: ( variation ) => onVariation( {}, variation ) };
}

// Another size picked: the frame shows that size's model; the same size again does not reload it.
{
	const page = openProductPage( INSTANCE + '/model/size-s?embed=1' );
	page.pick( { prepress_open_model_url: INSTANCE + '/model/size-l?embed=1' } );
	assert.equal( page.frame.src, INSTANCE + '/model/size-l?embed=1' );
	page.pick( { prepress_open_model_url: INSTANCE + '/model/size-l?embed=1' } );
	assert.equal( page.frame.loads, 1 );
}

// A variation without a model keeps the one on screen.
{
	const page = openProductPage( INSTANCE + '/model/size-s?embed=1' );
	page.pick( { prepress_open_model_url: '' } );
	page.pick( {} );
	assert.equal( page.frame.src, INSTANCE + '/model/size-s?embed=1' );
	assert.equal( page.frame.loads, 0 );
}

console.log( 'connector: ok' );
