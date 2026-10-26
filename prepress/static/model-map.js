/*
 * How a 3D product model wears its template page, as maths: pure, so Node tests it (tests/model_map.test.js);
 * product-model.js applies it to a three.js scene, the admin's pairing view derives it, /model/<token> reads it.
 *
 * A FACE is one UV island of one mesh — the triangles joined by shared vertex indices, which is where a modeller's
 * seams split a printed piece from the next. A PAIR is one point the admin clicked on the model and the same point
 * clicked on the page. From three or more pairs on a face, `fitAffine` finds the affine map from the model's own
 * coordinates onto the page, and `remap` writes that page position for every vertex of the face.
 *
 * Frames, fixed here so the pairing view, the stored map, the viewer and the export agree:
 *   - the model's coordinates are its UV (u, v) as the file carries them when the mesh has any, else the vertex's
 *     LOCAL (x, y, z) — a planar projection, enough for a flat panel and no good for a tube;
 *   - the page is in FRACTIONS: 0..1 across, 0..1 DOWN from the top edge, the glTF texture frame (flipY = false).
 * An affine is stored as two rows of (dims + 1) numbers: page_u = row0 · [from…, 1], page_v = row1 · [from…, 1].
 */
( function ( root ) {
	const EPSILON = 1e-12;

	/** Which island each triangle belongs to: { ofTriangle: Int32Array, count }. Islands are numbered in the
	 * order their first triangle appears, so the same file always gives the same numbers. */
	function islands( index, vertexCount ) {
		const parent = new Int32Array( vertexCount );
		for ( let i = 0; i < vertexCount; i++ ) {
			parent[ i ] = i;
		}
		const find = ( a ) => {
			while ( parent[ a ] !== a ) {
				parent[ a ] = parent[ parent[ a ] ];
				a = parent[ a ];
			}
			return a;
		};
		const union = ( a, b ) => {
			const ra = find( a );
			const rb = find( b );
			if ( ra !== rb ) {
				parent[ ra ] = rb;
			}
		};
		const triangles = Math.floor( index.length / 3 );
		for ( let t = 0; t < triangles; t++ ) {
			union( index[ 3 * t ], index[ 3 * t + 1 ] );
			union( index[ 3 * t ], index[ 3 * t + 2 ] );
		}
		const numbered = new Map();
		const ofTriangle = new Int32Array( triangles );
		for ( let t = 0; t < triangles; t++ ) {
			const rootVertex = find( index[ 3 * t ] );
			if ( ! numbered.has( rootVertex ) ) {
				numbered.set( rootVertex, numbered.size );
			}
			ofTriangle[ t ] = numbered.get( rootVertex );
		}
		return { ofTriangle, count: numbered.size };
	}

	/** Gaussian elimination with partial pivoting; null when the system is singular. */
	function solve( matrix, vector ) {
		const n = vector.length;
		const rows = matrix.map( ( row, i ) => [ ...row, vector[ i ] ] );
		for ( let col = 0; col < n; col++ ) {
			let pivot = col;
			for ( let r = col + 1; r < n; r++ ) {
				if ( Math.abs( rows[ r ][ col ] ) > Math.abs( rows[ pivot ][ col ] ) ) {
					pivot = r;
				}
			}
			if ( Math.abs( rows[ pivot ][ col ] ) < EPSILON ) {
				return null;
			}
			[ rows[ col ], rows[ pivot ] ] = [ rows[ pivot ], rows[ col ] ];
			for ( let r = 0; r < n; r++ ) {
				if ( r === col ) {
					continue;
				}
				const factor = rows[ r ][ col ] / rows[ col ][ col ];
				for ( let c = col; c <= n; c++ ) {
					rows[ r ][ c ] -= factor * rows[ col ][ c ];
				}
			}
		}
		return rows.map( ( row, i ) => row[ n ] / row[ i ] );
	}

	/**
	 * The least-squares affine from `pairs` ([{ from: [u, v] | [x, y, z], to: [pageU, pageV] }]) onto the page:
	 * { affine: [row0, row1], residual: [maxErrU, maxErrV] } in page fractions, or null when there are too few pairs
	 * (dims + 1) or they lie on a line (or, in 3D, a line or a plane through the origin's view — a singular fit).
	 */
	function fitAffine( pairs ) {
		if ( ! pairs.length ) {
			return null;
		}
		const dims = pairs[ 0 ].from.length;
		if ( pairs.length < dims + 1 || pairs.some( ( pair ) => pair.from.length !== dims ) ) {
			return null;
		}
		const columns = dims + 1;
		const normal = Array.from( { length: columns }, () => new Array( columns ).fill( 0 ) );
		const right = [ new Array( columns ).fill( 0 ), new Array( columns ).fill( 0 ) ];
		for ( const pair of pairs ) {
			const row = [ ...pair.from, 1 ];
			for ( let i = 0; i < columns; i++ ) {
				for ( let j = 0; j < columns; j++ ) {
					normal[ i ][ j ] += row[ i ] * row[ j ];
				}
				right[ 0 ][ i ] += row[ i ] * pair.to[ 0 ];
				right[ 1 ][ i ] += row[ i ] * pair.to[ 1 ];
			}
		}
		let affine = [ solve( normal, right[ 0 ] ), solve( normal, right[ 1 ] ) ];
		if ( ( ! affine[ 0 ] || ! affine[ 1 ] ) && dims === 3 && ! collinear( pairs.map( ( pair ) => pair.from ) ) ) {
			// A flat panel's points all lie on one plane, so a 3D affine is underdetermined: the gradient has a
			// free component along the plane's normal. A whisper of ridge picks the smallest gradient — the
			// orthogonal projection onto the panel — which is the only one that means anything off the plane.
			const trace = normal.reduce( ( sum, row, i ) => sum + row[ i ], 0 );
			for ( let i = 0; i < columns; i++ ) {
				normal[ i ][ i ] += 1e-9 * trace / columns;
			}
			affine = [ solve( normal, right[ 0 ] ), solve( normal, right[ 1 ] ) ];
		}
		if ( ! affine[ 0 ] || ! affine[ 1 ] ) {
			return null;
		}
		const residual = [ 0, 0 ];
		for ( const pair of pairs ) {
			const mapped = applyAffine( affine, pair.from );
			residual[ 0 ] = Math.max( residual[ 0 ], Math.abs( mapped[ 0 ] - pair.to[ 0 ] ) );
			residual[ 1 ] = Math.max( residual[ 1 ], Math.abs( mapped[ 1 ] - pair.to[ 1 ] ) );
		}
		return { affine, residual };
	}

	/** Whether 3D points all lie on one line (then no plane, and no projection, can be read off them). */
	function collinear( points ) {
		const [ first, ...rest ] = points;
		const edges = rest.map( ( point ) => point.map( ( value, k ) => value - first[ k ] ) );
		const scale = Math.max( ...edges.map( ( edge ) => Math.hypot( ...edge ) ) ) || 1;
		for ( let i = 0; i < edges.length; i++ ) {
			for ( let j = i + 1; j < edges.length; j++ ) {
				const [ a, b ] = [ edges[ i ], edges[ j ] ];
				const cross = [ a[ 1 ] * b[ 2 ] - a[ 2 ] * b[ 1 ], a[ 2 ] * b[ 0 ] - a[ 0 ] * b[ 2 ], a[ 0 ] * b[ 1 ] - a[ 1 ] * b[ 0 ] ];
				if ( Math.hypot( ...cross ) > 1e-9 * scale * scale ) {
					return false;
				}
			}
		}
		return true;
	}

	function applyAffine( affine, from ) {
		return affine.map( ( row ) => row[ row.length - 1 ] + from.reduce( ( sum, value, i ) => sum + row[ i ] * value, 0 ) );
	}

	/**
	 * Write the page position of every vertex of island `island` into `target` (a flat array of 2 per vertex).
	 * `source` is the flat per-vertex array the affine was fitted on, `size` numbers per vertex (2 for UV, 3 for XYZ).
	 * Returns how many vertices were written.
	 */
	function remap( { source, size, index, ofTriangle, island, affine, target } ) {
		const done = new Set();
		for ( let t = 0; t < ofTriangle.length; t++ ) {
			if ( ofTriangle[ t ] !== island ) {
				continue;
			}
			for ( let corner = 0; corner < 3; corner++ ) {
				const vertex = index[ 3 * t + corner ];
				if ( done.has( vertex ) ) {
					continue;
				}
				done.add( vertex );
				const from = Array.from( { length: size }, ( _, k ) => source[ vertex * size + k ] );
				const [ u, v ] = applyAffine( affine, from );
				target[ 2 * vertex ] = u;
				target[ 2 * vertex + 1 ] = v;
			}
		}
		return done.size;
	}

	/** The index with the triangles of `mapped` islands first, so one geometry can draw the printed faces with the
	 * page and the rest with the modeller's own material: { index, mappedTriangles, order } — `order[n]` is the
	 * original number of the triangle now drawn n-th, for a pick on the regrouped mesh to find its island. */
	function regroup( index, ofTriangle, mapped ) {
		const first = [];
		const rest = [];
		for ( let t = 0; t < ofTriangle.length; t++ ) {
			( mapped.has( ofTriangle[ t ] ) ? first : rest ).push( t );
		}
		const order = first.concat( rest );
		const reordered = [];
		for ( const t of order ) {
			reordered.push( index[ 3 * t ], index[ 3 * t + 1 ], index[ 3 * t + 2 ] );
		}
		return { index: reordered, mappedTriangles: first.length, order };
	}

	/** The box [min0, min1, max0, max1] round the vertices of island `island`, read from the flat per-vertex
	 * `source` (`size` numbers per vertex; only the first two are boxed: u, v). Null for an island with no triangle. */
	function islandBox( { source, size, index, ofTriangle, island } ) {
		let box = null;
		for ( let t = 0; t < ofTriangle.length; t++ ) {
			if ( ofTriangle[ t ] !== island ) {
				continue;
			}
			for ( let corner = 0; corner < 3; corner++ ) {
				const vertex = index[ 3 * t + corner ];
				const u = source[ vertex * size ];
				const v = source[ vertex * size + 1 ];
				box = box ? [ Math.min( box[ 0 ], u ), Math.min( box[ 1 ], v ), Math.max( box[ 2 ], u ), Math.max( box[ 3 ], v ) ]
					: [ u, v, u, v ];
			}
		}
		return box;
	}

	/* The eight ways a rectangle can be laid on another: four turns, each as drawn or mirrored. Listed upright and
	 * unmirrored first, because on a tie that is what an export almost always is. */
	const ORIENTATIONS = [ 0, 1, 2, 3 ].flatMap( ( turn ) => [ { turn, mirror: false }, { turn, mirror: true } ] );

	const corners = ( [ a0, a1, b0, b1 ] ) => [ [ a0, a1 ], [ b0, a1 ], [ b0, b1 ], [ a0, b1 ] ];
	// Aspect errors closer than this (5 % of the ratio) are a tie, settled by size order instead.
	const ASPECT_TIE = 0.05;

	/**
	 * The affine that lays an island's UV box onto a page box, in the orientation whose aspect fits best:
	 * { affine, turn, mirror, aspectError }. Boxes are [min0, min1, max0, max1]; the page box in page fractions.
	 * A turn of 1 is 90° clockwise on the page; the aspect error is |ln(island aspect / page aspect)| after the turn.
	 */
	function boxFit( islandBox, pageBox ) {
		// An island whose UVs collapse to a line or a point (an unprinted part a modeller never unwrapped)
		// has no box to lay anywhere.
		if ( islandBox[ 2 ] - islandBox[ 0 ] < 1e-9 || islandBox[ 3 ] - islandBox[ 1 ] < 1e-9 ) {
			return null;
		}
		const from = corners( islandBox );
		const to = corners( pageBox );
		const islandAspect = ( islandBox[ 2 ] - islandBox[ 0 ] ) / ( islandBox[ 3 ] - islandBox[ 1 ] || 1e-9 ) || 1e-9;
		const pageAspect = ( pageBox[ 2 ] - pageBox[ 0 ] ) / ( pageBox[ 3 ] - pageBox[ 1 ] || 1e-9 ) || 1e-9;
		let best = null;
		for ( const orientation of ORIENTATIONS ) {
			const turned = orientation.turn % 2 ? 1 / islandAspect : islandAspect;
			const aspectError = Math.abs( Math.log( turned / pageAspect ) );
			if ( best && aspectError >= best.aspectError - 1e-9 ) {
				continue;
			}
			const pairs = from.map( ( corner, i ) => {
				const step = orientation.mirror ? - i : i;
				return { from: corner, to: to[ ( ( orientation.turn + step ) % 4 + 4 ) % 4 ] };
			} );
			const fit = fitAffine( pairs );
			if ( fit ) {
				best = { affine: fit.affine, turn: orientation.turn, mirror: orientation.mirror, aspectError };
			}
		}
		return best;
	}

	/**
	 * Every island laid on the page box its shape fits best — the automatic pass, before anyone clicks.
	 * `islands`: [{ key, box }], `pageBoxes`: [[min0, min1, max0, max1], …] in page fractions. Biggest islands choose
	 * first; a page box is used once while there are boxes to spare, and shared when there are not (two faces of a
	 * wall on one page). Returns [{ key, pageBox: index, affine, turn, mirror, aspectError }].
	 */
	function autoFit( islandList, pageBoxes ) {
		if ( ! pageBoxes.length ) {
			return [];
		}
		const area = ( box ) => ( box[ 2 ] - box[ 0 ] ) * ( box[ 3 ] - box[ 1 ] );
		const ordered = [ ...islandList ].filter( ( one ) => one.box ).sort( ( a, b ) => area( b.box ) - area( a.box ) );
		const used = new Set();
		return ordered.map( ( island ) => {
			const free = pageBoxes.map( ( _, i ) => i ).filter( ( i ) => ! used.has( i ) );
			// Biggest box first, and a box only loses to a CLEARLY better aspect: two square boxes on
			// a page go to the two islands in size order, not both to the first listed.
			const candidates = ( free.length ? free : pageBoxes.map( ( _, i ) => i ) )
				.sort( ( a, b ) => area( pageBoxes[ b ] ) - area( pageBoxes[ a ] ) );
			let best = null;
			for ( const i of candidates ) {
				const fit = boxFit( island.box, pageBoxes[ i ] );
				if ( fit && ( ! best || fit.aspectError < best.aspectError - ASPECT_TIE ) ) {
					best = { ...fit, pageBox: i };
				}
			}
			if ( ! best ) {
				return null;
			}
			used.add( best.pageBox );
			return { key: island.key, ...best };
		} ).filter( Boolean );
	}

	const api = { applyAffine, autoFit, boxFit, fitAffine, islandBox, islands, regroup, remap, solve };
	if ( typeof module !== 'undefined' && module.exports ) {
		module.exports = api;
	} else {
		root.PrepressModelMap = api;
	}
} )( typeof window !== 'undefined' ? window : globalThis );
