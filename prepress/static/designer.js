/*
 * The design editor (ROADMAP „Step 3: the design editor"): the customer sheet of one template as a canvas, where the visitor
 * places pictures and texts, drags, scales and turns them, and saves the result as the filled template page
 * (POST /api/designs/<token>, design.py). The canvas's pixel grid is the sheet's picture (sheet.png, the long edge
 * 2048 px), so a placement never depends on the screen. Geometry: designer-model.js. The sheet's lines show faint
 * under the elements as a guide; the saved artwork is the elements on white, with no lines on it.
 */
( function () {
	const model = window.PrepressDesignerModel;
	const SHEET_ALPHA = 0.22; // the sheet's lines under the design: a guide to the finished size and the safe area
	const HISTORY_DEPTH = 30;
	const FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif';
	const STORAGE_PREFIX = 'prepress-design:';
	const PICTURE_MAX_EDGE = 1024; // what is kept of an uploaded picture: plenty for a 2048 px sheet

	const loadImage = ( src ) =>
		new Promise( ( resolve, reject ) => {
			const image = new Image();
			image.onload = () => resolve( image );
			image.onerror = () => reject( new Error( 'picture failed to load' ) );
			image.src = src;
		} );

	/** The file as a PNG data URL at most PICTURE_MAX_EDGE on its long edge. */
	async function pictureData( file ) {
		const url = URL.createObjectURL( file );
		try {
			const image = await loadImage( url );
			const width = image.naturalWidth || 600; // an SVG without a size of its own
			const height = image.naturalHeight || 300;
			const scale = Math.min( 1, PICTURE_MAX_EDGE / Math.max( width, height ) );
			const canvas = Object.assign( document.createElement( 'canvas' ), { width: Math.round( width * scale ), height: Math.round( height * scale ) } );
			canvas.getContext( '2d' ).drawImage( image, 0, 0, canvas.width, canvas.height );
			return canvas.toDataURL( 'image/png' );
		} finally {
			URL.revokeObjectURL( url );
		}
	}

	function setup( main ) {
		const strings = JSON.parse( main.dataset.strings );
		const words = { image: strings.images, text: strings.texts };
		const token = main.dataset.token;
		const display = main.querySelector( '#design' );
		const displayContext = display.getContext( '2d' );
		const status = main.querySelector( '#status' );
		const textInput = main.querySelector( '#text' );
		const sizeInput = main.querySelector( '#size' );
		const fileInput = main.querySelector( '#file' );
		const colourInputs = [ ...main.querySelectorAll( 'input[name="colour"]' ) ];
		const buttons = Object.fromEntries( [ ...main.querySelectorAll( '[data-act]' ) ].map( ( button ) => [ button.dataset.act, button ] ) );
		const saved = main.querySelector( '#saved' );
		const pdfLink = main.querySelector( '#pdfLink' );
		const checkLink = main.querySelector( '#checkLink' );
		const modelLink = main.querySelector( '#modelLink' );
		const shopLink = main.querySelector( '#shopLink' ); // only when the server allowed the shop's return page

		let sheet = null; // the sheet's picture: the canvas's size and its faint background
		let artwork = null; // offscreen canvas at the sheet's size: the design itself
		let elements = [];
		let selectedId = null;
		let history = [];
		let drag = null; // { mode, start, origin } while the pointer is down on an element or a handle
		const pictures = new Map(); // src -> decoded Image, so a drag never decodes

		const selected = () => elements.find( ( element ) => element.id === selectedId ) || null;
		const colour = () => ( colourInputs.find( ( input ) => input.checked ) || colourInputs[ 0 ] ).value;
		const say = ( text, alert = false ) => {
			status.textContent = text;
			status.classList.toggle( 'alert', alert );
		};

		// ---- storage, one design per template ---------------------------------------------------------------------

		function load() {
			try {
				const kept = JSON.parse( window.localStorage.getItem( STORAGE_PREFIX + token ) || '[]' );
				return Array.isArray( kept ) ? kept : [];
			} catch ( error ) {
				return [];
			}
		}

		function save() {
			try {
				window.localStorage.setItem( STORAGE_PREFIX + token, JSON.stringify( elements ) );
			} catch ( error ) {
				console.warn( 'designer: not kept in this browser', error ); // a private window: the page still works
			}
		}

		// ---- drawing ----------------------------------------------------------------------------------------------

		function measureText( element ) {
			const context = artwork.getContext( '2d' );
			context.font = `700 ${ element.size }px ${ FONT }`;
			return { width: Math.max( 1, context.measureText( element.text ).width ), height: element.size * 1.2 };
		}

		function drawElement( context, element ) {
			context.save();
			context.translate( element.x, element.y );
			context.rotate( element.rotation );
			context.scale( element.scale, element.scale );
			if ( element.type === 'image' ) {
				const picture = pictures.get( element.src );
				if ( picture ) {
					context.drawImage( picture, -element.width / 2, -element.height / 2, element.width, element.height );
				}
			} else {
				context.font = `700 ${ element.size }px ${ FONT }`;
				context.fillStyle = element.colour;
				context.textAlign = 'center';
				context.textBaseline = 'middle';
				context.fillText( element.text, 0, 0 );
			}
			context.restore();
		}

		/** The design as it is saved: the elements on white, nothing of the sheet's lines. */
		function drawArtwork() {
			const context = artwork.getContext( '2d' );
			context.setTransform( 1, 0, 0, 1, 0, 0 );
			context.fillStyle = '#fff';
			context.fillRect( 0, 0, artwork.width, artwork.height );
			elements.forEach( ( element ) => drawElement( context, element ) );
		}

		/** The editor's view: the artwork, the sheet's lines faint over it, the selection's outline and handles. */
		function drawDisplay() {
			const zoom = display.width / artwork.width;
			displayContext.setTransform( 1, 0, 0, 1, 0, 0 );
			displayContext.clearRect( 0, 0, display.width, display.height );
			displayContext.drawImage( artwork, 0, 0, display.width, display.height );
			displayContext.globalAlpha = SHEET_ALPHA;
			displayContext.drawImage( sheet, 0, 0, display.width, display.height );
			displayContext.globalAlpha = 1;
			const element = selected();
			if ( ! element ) {
				return;
			}
			displayContext.save();
			displayContext.scale( zoom, zoom );
			displayContext.lineWidth = 2 / zoom;
			displayContext.strokeStyle = '#1f6b33';
			const points = model.corners( element );
			displayContext.beginPath();
			points.forEach( ( point, index ) => ( index ? displayContext.lineTo( point.x, point.y ) : displayContext.moveTo( point.x, point.y ) ) );
			displayContext.closePath();
			displayContext.stroke();
			const handles = model.handles( element );
			const top = model.canvasPoint( element, { x: 0, y: -element.height / 2 } );
			displayContext.beginPath();
			displayContext.moveTo( top.x, top.y );
			displayContext.lineTo( handles.rotate.x, handles.rotate.y );
			displayContext.stroke();
			const radius = 9 / zoom;
			[ handles.scale, handles.rotate ].forEach( ( handle ) => {
				displayContext.beginPath();
				displayContext.arc( handle.x, handle.y, radius, 0, Math.PI * 2 );
				displayContext.fillStyle = '#fff';
				displayContext.fill();
				displayContext.stroke();
			} );
			displayContext.restore();
		}

		function fitDisplay() {
			const width = Math.max( 1, Math.round( display.clientWidth * window.devicePixelRatio ) );
			display.width = width;
			display.height = Math.round( ( width * artwork.height ) / artwork.width );
			display.style.height = `${ display.clientWidth * ( artwork.height / artwork.width ) }px`;
		}

		function render() {
			elements = elements.map( ( element ) => ( element.type === 'text' ? { ...element, ...measureText( element ) } : element ) );
			drawArtwork();
			drawDisplay();
			buttons.remove.disabled = ! selected();
			buttons.save.disabled = buttons.clear.disabled = ! elements.length;
			buttons.undo.disabled = ! history.length;
		}

		// ---- edits, each one undoable --------------------------------------------------------------------------------

		function commit( next, { select } = {} ) {
			history = [ ...history.slice( -( HISTORY_DEPTH - 1 ) ), elements ];
			elements = next;
			if ( select !== undefined ) {
				selectedId = select;
			}
			save();
			render();
			saved.classList.remove( 'on' ); // a changed design is not the saved one
		}

		async function addPicture( src ) {
			if ( ! pictures.has( src ) ) {
				pictures.set( src, await loadImage( src ) );
			}
			const picture = pictures.get( src );
			const element = model.newImage( src, picture.naturalWidth, picture.naturalHeight, artwork.width, artwork.height );
			commit( [ ...elements, element ], { select: element.id } );
			say( strings.hint );
		}

		const textSize = () => Math.round( ( artwork.width / 12 ) * ( Number( sizeInput.value ) / 100 ) ); // the slider: 20–200 % of a twelfth of the sheet's width

		function addText() {
			const text = textInput.value.trim();
			if ( ! text ) {
				textInput.focus();
				return;
			}
			const element = model.newText( text, colour(), textSize(), artwork.width, artwork.height );
			commit( [ ...elements, { ...element, ...measureText( element ) } ], { select: element.id } );
			textInput.value = '';
			say( strings.hint );
		}

		// ---- the pointer -------------------------------------------------------------------------------------------

		/** The pointer's place in the sheet's pixels. */
		function sheetPoint( event ) {
			const rect = display.getBoundingClientRect();
			return { x: ( ( event.clientX - rect.left ) / rect.width ) * artwork.width, y: ( ( event.clientY - rect.top ) / rect.height ) * artwork.height };
		}

		display.addEventListener( 'pointerdown', ( event ) => {
			if ( ! artwork ) {
				return;
			}
			const point = sheetPoint( event );
			const zoom = display.getBoundingClientRect().width / artwork.width;
			const current = selected();
			const handle = current && model.handleAt( current, point, zoom );
			const hit = handle ? current : model.hitTest( elements, point );
			if ( ! hit ) {
				selectedId = null;
				render();
				return;
			}
			selectedId = hit.id;
			if ( ! handle ) {
				elements = model.raise( elements, hit.id ); // the grabbed element comes to the top, as the eye expects
			}
			history = [ ...history.slice( -( HISTORY_DEPTH - 1 ) ), elements ];
			drag = { mode: handle || 'move', start: point, origin: selected() };
			display.setPointerCapture( event.pointerId );
			render();
			event.preventDefault();
		} );

		display.addEventListener( 'pointermove', ( event ) => {
			if ( ! drag ) {
				return;
			}
			const moved = model.clamped( model.dragged( drag.origin, drag.mode, drag.start, sheetPoint( event ) ), artwork.width, artwork.height );
			elements = model.replace( elements, moved );
			render();
		} );

		const endDrag = () => {
			if ( ! drag ) {
				return;
			}
			drag = null;
			save();
			saved.classList.remove( 'on' );
			render();
		};
		display.addEventListener( 'pointerup', endDrag );
		display.addEventListener( 'pointercancel', endDrag );

		display.addEventListener( 'keydown', ( event ) => {
			const element = selected();
			if ( ! element ) {
				return;
			}
			const step = event.shiftKey ? 40 : 8;
			const nudges = { ArrowLeft: [ -step, 0 ], ArrowRight: [ step, 0 ], ArrowUp: [ 0, -step ], ArrowDown: [ 0, step ] };
			if ( nudges[ event.key ] ) {
				const [ dx, dy ] = nudges[ event.key ];
				commit( model.replace( elements, model.clamped( { ...element, x: element.x + dx, y: element.y + dy }, artwork.width, artwork.height ) ) );
				event.preventDefault();
			} else if ( event.key === 'Delete' || event.key === 'Backspace' ) {
				commit( model.remove( elements, element.id ), { select: null } );
				event.preventDefault();
			}
		} );

		// ---- the toolbar ---------------------------------------------------------------------------------------------

		fileInput.addEventListener( 'change', async () => {
			const file = fileInput.files[ 0 ];
			fileInput.value = ''; // the same file again is a fresh change
			if ( ! file ) {
				return;
			}
			try {
				await addPicture( await pictureData( file ) );
			} catch ( error ) {
				console.error( error );
				say( strings.badFile, true );
			}
		} );
		buttons.text.addEventListener( 'click', addText );
		textInput.addEventListener( 'keydown', ( event ) => {
			if ( event.key === 'Enter' ) {
				event.preventDefault();
				addText();
			}
		} );
		// A colour or size change recolours or resizes the selected text, or sets what the next one gets.
		colourInputs.forEach( ( input ) => input.addEventListener( 'change', () => {
			const element = selected();
			if ( element && element.type === 'text' ) {
				commit( model.replace( elements, { ...element, colour: colour() } ) );
			}
		} ) );
		sizeInput.addEventListener( 'input', () => {
			const element = selected();
			if ( element && element.type === 'text' ) {
				elements = model.replace( elements, { ...element, size: textSize() } );
				render();
			}
		} );
		sizeInput.addEventListener( 'change', save );
		buttons.remove.addEventListener( 'click', () => {
			const element = selected();
			if ( element ) {
				commit( model.remove( elements, element.id ), { select: null } );
			}
		} );
		buttons.undo.addEventListener( 'click', () => {
			if ( history.length ) {
				elements = history.pop();
				selectedId = elements.some( ( element ) => element.id === selectedId ) ? selectedId : null;
				save();
				render();
			}
		} );
		buttons.clear.addEventListener( 'click', () => commit( [], { select: null } ) );

		/** The shop's page with the saved design named on it (the wordpress/ connector reads these three): the held
		 * token, the template, and the design's picture as an absolute address, since the shop is another origin. */
		function shopReturnUrl( result ) {
			const url = new URL( main.dataset.return );
			url.searchParams.set( 'design', result.held );
			url.searchParams.set( 'template', token );
			url.searchParams.set( 'png', new URL( result.png, window.location.href ).href );
			return url.href;
		}

		/** The artwork to the server, which makes it the filled template page and holds it: then the PDF, the check and the 3D links. */
		buttons.save.addEventListener( 'click', async () => {
			if ( ! elements.length ) {
				say( strings.empty, true );
				return;
			}
			buttons.save.disabled = true;
			say( strings.saving );
			try {
				drawArtwork();
				const blob = await new Promise( ( resolve ) => artwork.toBlob( resolve, 'image/png' ) );
				const body = new FormData();
				body.append( 'artwork', blob, 'projekt.png' );
				body.append( 'elements', JSON.stringify( elements ) );
				const response = await fetch( main.dataset.save, { method: 'POST', body } );
				const result = await response.json().catch( () => ( {} ) );
				if ( ! response.ok ) {
					throw new Error( result.error || `HTTP ${ response.status }` );
				}
				// The server names where the held design goes next: the check page with it queued, the 3D scene wearing it.
				// A static file is live before the Python that serves it is restarted, so an older answer may lack these.
				pdfLink.href = result.pdf;
				checkLink.href = result.check || checkLink.href;
				if ( modelLink && result.model ) {
					modelLink.href = result.model;
				}
				if ( shopLink && result.png ) {
					shopLink.href = shopReturnUrl( result );
				}
				saved.classList.add( 'on' );
				say( strings.saved );
			} catch ( error ) {
				console.error( error );
				say( error.message || strings.failed, true );
			} finally {
				buttons.save.disabled = ! elements.length;
			}
		} );

		// ---- opening -------------------------------------------------------------------------------------------------

		async function open() {
			say( strings.loading );
			sheet = await loadImage( main.dataset.sheet );
			artwork = Object.assign( document.createElement( 'canvas' ), { width: sheet.naturalWidth, height: sheet.naturalHeight } );
			elements = load();
			await Promise.all( elements.filter( ( element ) => element.type === 'image' && ! pictures.has( element.src ) ).map( async ( element ) => pictures.set( element.src, await loadImage( element.src ) ) ) );
			fitDisplay();
			render();
			say( strings.hint );
		}

		window.addEventListener( 'resize', () => {
			if ( artwork ) {
				fitDisplay();
				drawDisplay();
			}
		} );
		open().catch( ( error ) => {
			console.error( error );
			say( strings.failed, true );
		} );
	}

	const main = document.getElementById( 'editor' );
	if ( main ) {
		setup( main );
	}
} )();
