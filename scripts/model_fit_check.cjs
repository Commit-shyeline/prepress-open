#!/usr/bin/env node
/*
 * node scripts/model_fit_check.cjs model.glb [more.glb …] [--min-triangles N] [--boxes x0,y0,x1,y1;…]
 *
 * The oracle for the automatic fit (prepress/static/model-map.js autoFit): on a model whose UV islands
 * ALREADY lie where the page has their pieces, laying each island on its own box must give the identity —
 * upright, unmirrored, every island on its own box. Run it on models a 3D tool built with the page as
 * their texture, and a mismatch is a bug in the fit, not in the model.
 *
 * Without --boxes the page boxes are the islands' own UV boxes (the die's panels drawn exactly where the
 * modeller put them); with --boxes they are the page's real regions in page fractions, top down.
 * Islands under --min-triangles (default 12: caps, stubs, feet) are listed but not fitted.
 * Reads a plain glTF 2.0 binary: float UVs, any index type; a Draco-compressed or quantized file is refused.
 */
const fs = require( 'fs' );
const path = require( 'path' );
const maths = require( '../prepress/static/model-map.js' );

const COMPONENT = { 5120: Int8Array, 5121: Uint8Array, 5122: Int16Array, 5123: Uint16Array, 5125: Uint32Array, 5126: Float32Array };
const SIZE = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 };

function readGlb( file ) {
	const bytes = fs.readFileSync( file );
	if ( bytes.toString( 'latin1', 0, 4 ) !== 'glTF' || bytes.readUInt32LE( 4 ) !== 2 ) {
		throw new Error( 'not a glTF 2.0 binary' );
	}
	let offset = 12;
	let json = null;
	let bin = null;
	while ( offset < bytes.length ) {
		const length = bytes.readUInt32LE( offset );
		const type = bytes.readUInt32LE( offset + 4 );
		const chunk = bytes.subarray( offset + 8, offset + 8 + length );
		if ( type === 0x4E4F534A ) {
			json = JSON.parse( chunk.toString( 'utf8' ) );
		} else if ( type === 0x004E4942 ) {
			bin = chunk;
		}
		offset += 8 + length;
	}
	if ( ( json.extensionsRequired || [] ).includes( 'KHR_draco_mesh_compression' ) ) {
		throw new Error( 'Draco-compressed: run the check on the uncompressed export' );
	}
	return { json, bin };
}

function accessor( glb, index ) {
	const acc = glb.json.accessors[ index ];
	const view = glb.json.bufferViews[ acc.bufferView ];
	const Type = COMPONENT[ acc.componentType ];
	const size = SIZE[ acc.type ];
	const stride = view.byteStride || size * Type.BYTES_PER_ELEMENT;
	const start = ( view.byteOffset || 0 ) + ( acc.byteOffset || 0 );
	const out = new Float64Array( acc.count * size );
	for ( let i = 0; i < acc.count; i++ ) {
		const at = glb.bin.byteOffset + start + i * stride;
		const row = new Type( glb.bin.buffer.slice( at, at + size * Type.BYTES_PER_ELEMENT ) );
		for ( let k = 0; k < size; k++ ) {
			out[ i * size + k ] = acc.normalized ? row[ k ] / ( Type === Uint16Array ? 65535 : Type === Uint8Array ? 255 : 1 ) : row[ k ];
		}
	}
	return { data: out, size, count: acc.count, normalized: !! acc.normalized, componentType: acc.componentType };
}

function checkModel( file, options ) {
	const glb = readGlb( file );
	const report = { file: path.basename( file ), meshes: 0, islands: 0, fitted: 0, wrong: [] };
	let primitiveIndex = 0;
	for ( const mesh of glb.json.meshes || [] ) {
		for ( const primitive of mesh.primitives ) {
			const name = `${ mesh.name || 'mesh' }#${ primitiveIndex++ }`;
			if ( primitive.attributes.TEXCOORD_0 === undefined || primitive.indices === undefined ) {
				continue;
			}
			const uv = accessor( glb, primitive.attributes.TEXCOORD_0 );
			if ( uv.componentType !== 5126 && ! uv.normalized ) {
				throw new Error( `${ name }: quantized UVs are not read here` );
			}
			const index = Array.from( accessor( glb, primitive.indices ).data );
			const found = maths.islands( index, uv.count );
			report.meshes++;
			const islands = [];
			for ( let island = 0; island < found.count; island++ ) {
				const triangles = found.ofTriangle.reduce( ( n, which ) => n + ( which === island ? 1 : 0 ), 0 );
				const box = maths.islandBox( { source: uv.data, size: 2, index, ofTriangle: found.ofTriangle, island } );
				islands.push( { key: `${ name }:${ island }`, box, triangles } );
			}
			report.islands += islands.length;
			const big = islands.filter( ( one ) => one.triangles >= options.minTriangles );
			const boxes = options.boxes || big.map( ( one ) => one.box );
			const fits = maths.autoFit( big, boxes );
			for ( const fit of fits ) {
				report.fitted++;
				const own = big.findIndex( ( one ) => one.key === fit.key );
				const expectedBox = options.boxes ? null : own;
				const identity = fit.affine.every( ( row, r ) => row.every( ( value, c ) => Math.abs( value - ( r === c ? 1 : 0 ) ) < 1e-6 ) );
				const problems = [];
				if ( fit.turn !== 0 || fit.mirror ) {
					problems.push( `turn ${ fit.turn * 90 }°${ fit.mirror ? ' mirrored' : '' }` );
				}
				if ( expectedBox !== null && fit.pageBox !== expectedBox ) {
					problems.push( `laid on box ${ fit.pageBox }, its own is ${ expectedBox }` );
				}
				if ( expectedBox !== null && ! identity ) {
					problems.push( `affine ${ JSON.stringify( fit.affine.map( ( row ) => row.map( ( v ) => +v.toFixed( 4 ) ) ) ) }` );
				}
				if ( problems.length ) {
					report.wrong.push( `${ fit.key } (${ big[ own ].triangles } tri): ${ problems.join( ', ' ) }` );
				}
			}
		}
	}
	return report;
}

function main() {
	const args = process.argv.slice( 2 );
	const options = { minTriangles: 12, boxes: null };
	const files = [];
	for ( let i = 0; i < args.length; i++ ) {
		if ( args[ i ] === '--min-triangles' ) {
			options.minTriangles = Number( args[ ++ i ] );
		} else if ( args[ i ] === '--boxes' ) {
			options.boxes = args[ ++ i ].split( ';' ).map( ( box ) => box.split( ',' ).map( Number ) );
		} else {
			files.push( args[ i ] );
		}
	}
	if ( ! files.length ) {
		console.error( 'usage: node scripts/model_fit_check.cjs model.glb [...] [--min-triangles N] [--boxes x0,y0,x1,y1;...]' );
		process.exit( 2 );
	}
	let failed = 0;
	for ( const file of files ) {
		try {
			const report = checkModel( file, options );
			const verdict = report.wrong.length ? 'WRONG' : 'ok';
			console.log( `${ verdict.padEnd( 5 ) } ${ report.file }: ${ report.meshes } meshes, ${ report.islands } islands, ${ report.fitted } fitted` );
			for ( const line of report.wrong ) {
				console.log( `      ${ line }` );
			}
			failed += report.wrong.length ? 1 : 0;
		} catch ( error ) {
			console.log( `SKIP  ${ path.basename( file ) }: ${ error.message }` );
		}
	}
	process.exit( failed ? 1 : 0 );
}

main();
