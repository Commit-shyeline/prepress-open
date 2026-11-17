/*
 * prepress-open Connector — the hand-off reader (GPLv2 or later, see LICENSE).
 *
 * The instance's editor sends the customer back to the shop page that linked it, with the saved
 * design named in the URL: `?design=<held token>&template=<template token>&png=<picture address>`.
 * This script reads those three, keeps them in sessionStorage, takes them off the URL and tells the
 * page: `document` gets a `prepress-open:design` event with `{ held, template, png, pdf }`, so the
 * shop's own script (a basket, an inquiry form) can carry the design on. The held token lives on the
 * instance for 30 minutes after its last use; the PDF address is where the design can be fetched
 * server side within that time.
 */
/*
 * The 3D preview follows the chosen variation: WooCommerce announces a pick with its jQuery event
 * `found_variation`, whose data carries `prepress_open_model_url` (the plugin's PHP adds it). A
 * variation without a model leaves the frame on the one it shows.
 */
( function () {
	const jQuery = window.jQuery;
	if ( ! jQuery ) {
		return; // no WooCommerce variation form speaks without it
	}
	jQuery( document ).on( 'found_variation', function ( event, variation ) {
		const url = variation && variation.prepress_open_model_url;
		if ( ! url ) {
			return;
		}
		document.querySelectorAll( 'iframe.prepress-open-model' ).forEach( ( frame ) => {
			if ( frame.getAttribute( 'src' ) !== url ) {
				frame.setAttribute( 'src', url );
			}
		} );
	} );
} )();

( function () {
	const connector = window.PrepressOpenConnector || {};
	const instance = String( connector.instance || '' ).replace( /\/+$/, '' );
	const params = new URLSearchParams( window.location.search );
	const held = params.get( 'design' );
	const template = params.get( 'template' );
	if ( ! held || ! template || ! /^[A-Za-z0-9_-]+$/.test( held ) || ! /^[A-Za-z0-9_-]+$/.test( template ) ) {
		return;
	}
	// Only a picture served by the configured instance is passed on: the URL came from outside.
	const png = params.get( 'png' ) || '';
	const design = {
		held,
		template,
		png: instance && png.startsWith( instance + '/' ) ? png : '',
		pdf: instance ? `${ instance }/api/designs/${ encodeURIComponent( held ) }/pdf` : '',
	};
	try {
		window.sessionStorage.setItem( connector.storageKey || 'prepress-open:design', JSON.stringify( design ) );
	} catch ( error ) {
		// a private window: the event below still carries it
	}
	[ 'design', 'template', 'png' ].forEach( ( name ) => params.delete( name ) );
	const query = params.toString();
	window.history.replaceState( window.history.state, '', window.location.pathname + ( query ? `?${ query }` : '' ) + window.location.hash );
	const announce = () => document.dispatchEvent( new CustomEvent( 'prepress-open:design', { detail: design } ) );
	if ( document.readyState === 'loading' ) {
		document.addEventListener( 'DOMContentLoaded', announce );
	} else {
		announce();
	}
} )();
