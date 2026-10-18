/*
 * The design editor's geometry (docs/design-editor.md), pure so Node tests it (tests/designer_model.test.js);
 * designer.js draws it and handles the pointer. An element is a picture or a text placed on the template's page:
 * { id, type: 'image' | 'text', x, y, scale, rotation, width, height, … } where x, y is its centre in canvas pixels,
 * width × height its natural size at scale 1 (a picture's pixels, a text's measured box), rotation in radians.
 * Pictures carry `src` (a data URL), texts `text`, `colour` and `size` (px at scale 1).
 */
( function ( root ) {
	const HANDLE_RADIUS = 18; // canvas px round a handle that counts as grabbing it: a fingertip
	const ROTATE_HANDLE_OFFSET = 44; // canvas px above the top edge, where the rotate handle sits
	const MIN_SCALE = 0.05;
	const MAX_SCALE = 20;

	let counter = 0;
	const nextId = () => `e${ Date.now().toString( 36 ) }${ ( counter++ ).toString( 36 ) }`;

	/** A point in the element's own frame: relative to its centre, turned back by its rotation, at scale 1. */
	function localPoint( element, point ) {
		const dx = point.x - element.x;
		const dy = point.y - element.y;
		const cos = Math.cos( -element.rotation );
		const sin = Math.sin( -element.rotation );
		return { x: ( dx * cos - dy * sin ) / element.scale, y: ( dx * sin + dy * cos ) / element.scale };
	}

	/** The element's point `local` (its own frame) on the canvas. */
	function canvasPoint( element, local ) {
		const cos = Math.cos( element.rotation );
		const sin = Math.sin( element.rotation );
		const x = local.x * element.scale;
		const y = local.y * element.scale;
		return { x: element.x + x * cos - y * sin, y: element.y + x * sin + y * cos };
	}

	/** The element's four corners on the canvas, clockwise from the top left. */
	function corners( element ) {
		const w = element.width / 2;
		const h = element.height / 2;
		return [ { x: -w, y: -h }, { x: w, y: -h }, { x: w, y: h }, { x: -w, y: h } ].map( ( local ) => canvasPoint( element, local ) );
	}

	/** The axis-aligned box round the element on the canvas. */
	function bounds( element ) {
		const points = corners( element );
		const xs = points.map( ( point ) => point.x );
		const ys = points.map( ( point ) => point.y );
		return { left: Math.min( ...xs ), top: Math.min( ...ys ), right: Math.max( ...xs ), bottom: Math.max( ...ys ) };
	}

	/** Whether `point` lies on the element (inside its rotated box). */
	function contains( element, point ) {
		const local = localPoint( element, point );
		return Math.abs( local.x ) <= element.width / 2 && Math.abs( local.y ) <= element.height / 2;
	}

	/** The topmost element under `point` (the last in the list is drawn on top), or null. */
	function hitTest( elements, point ) {
		for ( let index = elements.length - 1; index >= 0; index-- ) {
			if ( contains( elements[ index ], point ) ) {
				return elements[ index ];
			}
		}
		return null;
	}

	/** Where the selected element's handles are on the canvas: scale at the bottom-right corner, rotate above the top edge. */
	function handles( element ) {
		return {
			scale: canvasPoint( element, { x: element.width / 2, y: element.height / 2 } ),
			rotate: canvasPoint( element, { x: 0, y: -element.height / 2 - ROTATE_HANDLE_OFFSET / element.scale } ),
		};
	}

	const distance = ( a, b ) => Math.hypot( a.x - b.x, a.y - b.y );

	/** Which handle of the selected element `point` grabs ('scale' | 'rotate'), or null. Handles win over the body.
	 * `zoom` is screen px per canvas px: the handle is drawn at a fixed screen size, so its reach on the canvas shrinks as the view zooms in. */
	function handleAt( element, point, zoom = 1 ) {
		const at = handles( element );
		const reach = HANDLE_RADIUS / zoom;
		if ( distance( at.rotate, point ) <= reach ) {
			return 'rotate';
		}
		if ( distance( at.scale, point ) <= reach ) {
			return 'scale';
		}
		return null;
	}

	/**
	 * The element after a pointer drag from `start` to `current` in `mode` ('move' | 'scale' | 'rotate'), given the
	 * element as it was when the drag began (`origin`). Scaling keeps the centre and the proportions; rotating turns
	 * about the centre by the angle the pointer swept.
	 */
	function dragged( origin, mode, start, current ) {
		if ( mode === 'move' ) {
			return { ...origin, x: origin.x + current.x - start.x, y: origin.y + current.y - start.y };
		}
		const centre = { x: origin.x, y: origin.y };
		if ( mode === 'scale' ) {
			const from = distance( centre, start ) || 1;
			const scale = Math.min( MAX_SCALE, Math.max( MIN_SCALE, ( origin.scale * distance( centre, current ) ) / from ) );
			return { ...origin, scale };
		}
		if ( mode === 'rotate' ) {
			const swept = Math.atan2( current.y - centre.y, current.x - centre.x ) - Math.atan2( start.y - centre.y, start.x - centre.x );
			return { ...origin, rotation: origin.rotation + swept };
		}
		return origin;
	}

	/** The element with its centre kept on the canvas, so nothing can be dragged out of reach. */
	function clamped( element, width, height ) {
		return { ...element, x: Math.min( width, Math.max( 0, element.x ) ), y: Math.min( height, Math.max( 0, element.y ) ) };
	}

	/** Polish plural forms: 1 grafika, 2–4 grafiki, 5+ grafik (another language passes its own forms). */
	function plural( count, forms ) {
		const tens = count % 100;
		if ( count === 1 ) {
			return forms[ 0 ];
		}
		return count % 10 >= 2 && count % 10 <= 4 && ( tens < 12 || tens > 14 ) ? forms[ 1 ] : forms[ 2 ];
	}

	/** „2 grafiki, 1 napis": what a design holds; '' for an empty one. `words` = { image: forms, text: forms }. */
	function summary( elements, words ) {
		const counts = { image: 0, text: 0 };
		elements.forEach( ( element ) => {
			counts[ element.type ] = ( counts[ element.type ] || 0 ) + 1;
		} );
		return [ 'image', 'text' ]
			.filter( ( type ) => counts[ type ] )
			.map( ( type ) => `${ counts[ type ] } ${ plural( counts[ type ], words[ type ] ) }` )
			.join( ', ' );
	}

	/** A new picture element centred on the canvas, scaled so it covers about a third of the canvas's width. */
	function newImage( src, naturalWidth, naturalHeight, canvasWidth, canvasHeight ) {
		const scale = Math.min( 1, ( canvasWidth / 3 ) / naturalWidth, ( canvasHeight / 3 ) / naturalHeight );
		return { id: nextId(), type: 'image', src, x: canvasWidth / 2, y: canvasHeight / 2, scale, rotation: 0, width: naturalWidth, height: naturalHeight };
	}

	/** A new text element centred on the canvas; `width`/`height` are set by the caller once measured. */
	function newText( text, colour, size, canvasWidth, canvasHeight ) {
		return { id: nextId(), type: 'text', text, colour, size, x: canvasWidth / 2, y: canvasHeight / 2, scale: 1, rotation: 0, width: size * text.length * 0.6, height: size * 1.2 };
	}

	const replace = ( elements, element ) => elements.map( ( each ) => ( each.id === element.id ? element : each ) );
	const remove = ( elements, id ) => elements.filter( ( each ) => each.id !== id );
	/** The element moved to the end of the list: drawn on top of the others. */
	const raise = ( elements, id ) => {
		const element = elements.find( ( each ) => each.id === id );
		return element ? [ ...remove( elements, id ), element ] : elements;
	};

	const api = { HANDLE_RADIUS, ROTATE_HANDLE_OFFSET, bounds, canvasPoint, clamped, contains, corners, dragged, handleAt, handles, hitTest, localPoint, newImage, newText, plural, raise, remove, replace, summary };
	if ( typeof module !== 'undefined' && module.exports ) {
		module.exports = api;
	} else {
		root.PrepressDesignerModel = api;
	}
} )( typeof window !== 'undefined' ? window : globalThis );
