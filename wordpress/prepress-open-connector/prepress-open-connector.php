<?php
/**
 * Plugin Name: prepress-open Connector
 * Plugin URI:  https://github.com/Commit-shyeline/prepress-open
 * Description: Links a running prepress-open instance from your shop: a "Design it online" button per product, and the saved design handed back into your page.
 * Version:     0.1.0
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
	$attributes = shortcode_atts( array( 'template' => '', 'label' => __( 'Design it online', 'prepress-open-connector' ), 'class' => '' ), $attributes, 'prepress_design' );
	$url        = prepress_open_design_url( (string) $attributes['template'] );
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
