<?php
/**
 * Plugin Name: prepress-open Connector
 * Plugin URI:  https://github.com/Commit-shyeline/prepress-open
 * Description: Links a running prepress-open instance from your shop: the product's 3D preview, a "Design it online" button per product, and the saved design handed back into your page.
 * Version:     0.2.0
 * Requires at least: 6.5
 * Requires PHP: 8.0
 * Author:      prepress-open contributors
 * License:     GPLv2 or later
 * License URI: https://www.gnu.org/licenses/gpl-2.0.html
 * Text Domain: prepress-open-connector
 *
 * The engine (Python) runs elsewhere; this plugin ships none of it. It knows the instance's address,
 * builds the editor's link for a template token with this page as the return address, and its script
 * reads the saved design's token back out of the URL the editor sends the customer to
 * (`?design=<held>&template=<token>&png=<picture>`) — see wordpress/README.md in the repository.
 */

defined( 'ABSPATH' ) || exit;

const PREPRESS_OPEN_OPTION = 'prepress_open_instance_url';
const PREPRESS_OPEN_STORAGE_KEY = 'prepress-open:design'; // sessionStorage, read by the shop's own scripts

/** The instance's address with the path it is mounted under, no trailing slash; '' until configured. */
function prepress_open_instance_url(): string {
	return rtrim( (string) get_option( PREPRESS_OPEN_OPTION, '' ), '/' );
}

/** One field under Settings → General. */
add_action( 'admin_init', 'prepress_open_register_setting' );
function prepress_open_register_setting(): void {
	register_setting(
		'general',
		PREPRESS_OPEN_OPTION,
		array( 'type' => 'string', 'sanitize_callback' => 'prepress_open_sanitize_url', 'default' => '' )
	);
	add_settings_field(
		PREPRESS_OPEN_OPTION,
		__( 'prepress-open instance', 'prepress-open-connector' ),
		'prepress_open_render_setting',
		'general'
	);
}

function prepress_open_render_setting(): void {
	printf(
		'<input type="url" class="regular-text code" name="%1$s" id="%1$s" value="%2$s" placeholder="https://check.example.com/sprawdz">',
		esc_attr( PREPRESS_OPEN_OPTION ),
		esc_attr( (string) get_option( PREPRESS_OPEN_OPTION, '' ) )
	);
	echo '<p class="description">' . esc_html__( 'Where your prepress-open instance answers, including the path it is mounted under. The instance must list this site in PREPRESS_RETURN_ORIGINS, or its editor shows no way back.', 'prepress-open-connector' ) . '</p>';
}

function prepress_open_sanitize_url( $value ): string {
	$url = esc_url_raw( trim( (string) $value ) );
	return preg_match( '#^https?://#i', $url ) ? rtrim( $url, '/' ) : '';
}

/**
 * The editor's address for one template token, returning to `$return_to` (this page by default).
 * '' when the instance is not configured or the token is not a token.
 */
function prepress_open_design_url( string $template_token, string $return_to = '' ): string {
	$instance = prepress_open_instance_url();
	if ( ! $instance || ! preg_match( '/^[A-Za-z0-9_-]{1,64}$/', $template_token ) ) {
		return '';
	}
	$return_to = $return_to ?: prepress_open_current_url();
	return $instance . '/projekt/' . rawurlencode( $template_token ) . '?' . http_build_query( array( 'return' => $return_to ) );
}

/** This request's page, without its query string: what the editor sends the customer back to. */
function prepress_open_current_url(): string {
	global $wp;
	return home_url( trailingslashit( (string) ( $wp->request ?? '' ) ) );
}

/**
 * `[prepress_design template="Wn5ikpfs" label="Design it online"]` → the button. The template token
 * is the instance's: the admin panel there shows it under each template.
 */
add_shortcode( 'prepress_design', 'prepress_open_design_shortcode' );
function prepress_open_design_shortcode( $attributes ): string {
	// `return`: the page to come back to when it is not this one, or when this one is query-driven
	// (`/preview/?product=gate`): the default keeps the path and drops the query.
	$attributes = shortcode_atts( array( 'template' => '', 'label' => __( 'Design it online', 'prepress-open-connector' ), 'class' => '', 'return' => '' ), $attributes, 'prepress_design' );
	$url        = prepress_open_design_url( (string) $attributes['template'], (string) $attributes['return'] );
	if ( ! $url ) {
		return '';
	}
	return sprintf(
		'<a class="prepress-open-design %s" href="%s" rel="noopener">%s</a>',
		esc_attr( (string) $attributes['class'] ),
		esc_url( $url ),
		esc_html( (string) $attributes['label'] )
	);
}

const PREPRESS_OPEN_PLACEMENT_OPTION = 'prepress_open_model_on_product';
const PREPRESS_OPEN_TEMPLATE_META    = '_prepress_open_template'; // on a product and on each of its variations
// Its own form name: the product's field and the variations' fields post in one form, and
// `name=x` beside `name=x[0]` leaves PHP only the array — the product's token would be dropped.
const PREPRESS_OPEN_VARIATION_FIELD  = 'variable_prepress_open_template';
const PREPRESS_OPEN_MODEL_HEIGHT_PX  = 480;

/** `$value` when it is a template token, else ''. */
function prepress_open_token( $value ): string {
	$token = trim( (string) $value );
	return preg_match( '/^[A-Za-z0-9_-]{1,64}$/', $token ) ? $token : '';
}

/** The 3D viewer's address for one template token, without the instance's own header; '' when unconfigured. */
function prepress_open_model_url( string $template_token ): string {
	$instance = prepress_open_instance_url();
	$token    = prepress_open_token( $template_token );
	return $instance && $token ? $instance . '/model/' . rawurlencode( $token ) . '?embed=1' : '';
}

/** The viewer's frame for `$url`; the script swaps its address when the customer picks another variation. */
function prepress_open_model_frame( string $url, int $height_px = PREPRESS_OPEN_MODEL_HEIGHT_PX, string $title = '' ): string {
	if ( ! $url ) {
		return '';
	}
	// Never taller than the screen it is on: a phone held sideways still shows the whole model.
	$style = sprintf( 'display:block;width:100%%;height:min(%dpx,85vh);border:0;', max( 200, $height_px ) );
	return sprintf(
		'<iframe class="prepress-open-model" src="%s" title="%s" loading="lazy" style="%s"></iframe>',
		esc_url( $url ),
		esc_attr( $title ?: __( '3D preview', 'prepress-open-connector' ) ),
		esc_attr( $style )
	);
}

/**
 * `[prepress_model template="Wn5ikpfs" height="480"]` → the 3D viewer. Without `template` on a
 * WooCommerce product page it shows that product's own model.
 */
add_shortcode( 'prepress_model', 'prepress_open_model_shortcode' );
function prepress_open_model_shortcode( $attributes ): string {
	$attributes = shortcode_atts( array( 'template' => '', 'height' => PREPRESS_OPEN_MODEL_HEIGHT_PX, 'title' => '' ), $attributes, 'prepress_model' );
	$token      = prepress_open_token( $attributes['template'] ) ?: prepress_open_product_template();
	return prepress_open_model_frame( prepress_open_model_url( $token ), (int) $attributes['height'], (string) $attributes['title'] );
}

/**
 * The template token of the WooCommerce product on this page: its own, else its first variation's
 * that has one (a product sold only in sizes has no model of its own). '' elsewhere.
 */
function prepress_open_product_template(): string {
	$product = function_exists( 'wc_get_product' ) && is_singular( 'product' ) ? wc_get_product( get_the_ID() ) : null;
	if ( ! $product ) {
		return '';
	}
	foreach ( array_merge( array( $product->get_id() ), $product->get_children() ) as $post_id ) {
		$token = prepress_open_token( get_post_meta( $post_id, PREPRESS_OPEN_TEMPLATE_META, true ) );
		if ( $token ) {
			return $token;
		}
	}
	return '';
}

/** The checkbox under Settings → General: show the model on every product page that has a template. */
add_action( 'admin_init', 'prepress_open_register_placement_setting' );
function prepress_open_register_placement_setting(): void {
	register_setting( 'general', PREPRESS_OPEN_PLACEMENT_OPTION, array( 'type' => 'boolean', 'sanitize_callback' => 'rest_sanitize_boolean', 'default' => false ) );
	add_settings_field( PREPRESS_OPEN_PLACEMENT_OPTION, __( 'prepress-open 3D preview', 'prepress-open-connector' ), 'prepress_open_render_placement_setting', 'general' );
}

function prepress_open_render_placement_setting(): void {
	printf(
		'<label><input type="checkbox" name="%1$s" value="1" %2$s> %3$s</label>',
		esc_attr( PREPRESS_OPEN_PLACEMENT_OPTION ),
		checked( (bool) get_option( PREPRESS_OPEN_PLACEMENT_OPTION, false ), true, false ),
		esc_html__( 'Show the 3D preview on WooCommerce product pages whose product (or variation) has a template token. Elsewhere use the [prepress_model] shortcode.', 'prepress-open-connector' )
	);
}

add_action( 'woocommerce_after_single_product_summary', 'prepress_open_model_on_product', 5 );
function prepress_open_model_on_product(): void {
	if ( get_option( PREPRESS_OPEN_PLACEMENT_OPTION, false ) ) {
		// The frame's parts are escaped where it is built (prepress_open_model_frame).
		echo prepress_open_model_frame( prepress_open_model_url( prepress_open_product_template() ) ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped
	}
}

/** The template token's field on the product ("General" tab) and on each variation. */
add_action( 'woocommerce_product_options_general_product_data', 'prepress_open_product_field' );
function prepress_open_product_field(): void {
	woocommerce_wp_text_input( prepress_open_template_field( PREPRESS_OPEN_TEMPLATE_META, get_the_ID() ) );
}

add_action( 'woocommerce_product_after_variable_attributes', 'prepress_open_variation_field', 10, 3 );
function prepress_open_variation_field( $loop, $variation_data, $variation ): void {
	woocommerce_wp_text_input(
		prepress_open_template_field( PREPRESS_OPEN_VARIATION_FIELD . '[' . (int) $loop . ']', $variation->ID ) + array( 'wrapper_class' => 'form-row form-row-full' )
	);
}

function prepress_open_template_field( string $field_name, int $post_id ): array {
	return array(
		'id'          => $field_name,
		'label'       => __( 'prepress-open template', 'prepress-open-connector' ),
		'description' => __( 'The template token whose 3D model this product shows.', 'prepress-open-connector' ),
		'desc_tip'    => true,
		'value'       => (string) get_post_meta( $post_id, PREPRESS_OPEN_TEMPLATE_META, true ),
	);
}

// WooCommerce has checked its own nonce and the user's right to edit before either of these runs.
add_action( 'woocommerce_process_product_meta', 'prepress_open_save_product_field' );
function prepress_open_save_product_field( $product_id ): void {
	// phpcs:ignore WordPress.Security.NonceVerification.Missing
	prepress_open_save_template( (int) $product_id, wp_unslash( $_POST[ PREPRESS_OPEN_TEMPLATE_META ] ?? '' ) );
}

add_action( 'woocommerce_save_product_variation', 'prepress_open_save_variation_field', 10, 2 );
function prepress_open_save_variation_field( $variation_id, $loop ): void {
	// phpcs:ignore WordPress.Security.NonceVerification.Missing
	prepress_open_save_template( (int) $variation_id, wp_unslash( $_POST[ PREPRESS_OPEN_VARIATION_FIELD ][ $loop ] ?? '' ) );
}

/** Keep a token, drop anything else: a value that is not a token would only build a dead address. */
function prepress_open_save_template( int $post_id, $value ): void {
	$token = prepress_open_token( is_string( $value ) ? $value : '' );
	if ( $token ) {
		update_post_meta( $post_id, PREPRESS_OPEN_TEMPLATE_META, $token );
	} else {
		delete_post_meta( $post_id, PREPRESS_OPEN_TEMPLATE_META );
	}
}

/** Each variation tells the page its own viewer address (connector.js swaps the frame to it). */
add_filter( 'woocommerce_available_variation', 'prepress_open_variation_model_url', 10, 3 );
function prepress_open_variation_model_url( $data, $product, $variation ) {
	$data['prepress_open_model_url'] = prepress_open_model_url( (string) get_post_meta( $variation->get_id(), PREPRESS_OPEN_TEMPLATE_META, true ) );
	return $data;
}

/**
 * The hand-off reader, on every page while an instance is configured: a return page is whatever
 * page carried the button, so the script cannot know in advance where the customer lands.
 */
add_action( 'wp_enqueue_scripts', 'prepress_open_enqueue_connector' );
function prepress_open_enqueue_connector(): void {
	$instance = prepress_open_instance_url();
	if ( ! $instance ) {
		return;
	}
	$script = plugin_dir_path( __FILE__ ) . 'connector.js';
	wp_enqueue_script( 'prepress-open-connector', plugins_url( 'connector.js', __FILE__ ), array(), (string) filemtime( $script ), true );
	wp_add_inline_script(
		'prepress-open-connector',
		'window.PrepressOpenConnector = ' . wp_json_encode( array( 'instance' => $instance, 'storageKey' => PREPRESS_OPEN_STORAGE_KEY ) ) . ';',
		'before'
	);
}
