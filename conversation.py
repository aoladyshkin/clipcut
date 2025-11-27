from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from commands import start, topup_start, broadcast_start, broadcast_message, cancel, start_feedback, broadcast_to_start, broadcast_to_message, broadcast_w_prices_start, broadcast_w_prices_message
from handlers import (
    get_url,
    url_entrypoint,
    get_shorts_number_auto,
    get_shorts_number_manual,
    get_layout,
    get_face_tracking,
    get_bottom_video,
    get_subtitles_type,
    get_subtitle_style,
    get_banner_choice,
    confirm_config,
    cancel_conversation,
    topup_stars,
    topup_crypto,
    topup_yookassa,
    get_yookassa_email,
    check_yookassa_payment,
    select_topup_package,
    back_to_package_selection,
    check_crypto_payment,
    handle_rating,
    handle_feedback,
    skip_feedback,
    handle_user_feedback,
    start_demo,
    confirm_demo,
    get_brainrot,
    handle_check_subscription_reward,
) 
from states import (
    GET_URL,
    GET_SUBTITLE_STYLE,
    GET_BOTTOM_VIDEO,
    GET_LAYOUT,
    GET_FACE_TRACKING,
    GET_SUBTITLES_TYPE,
    CONFIRM_CONFIG,
    GET_SHORTS_NUMBER,
    GET_TOPUP_METHOD,
    GET_TOPUP_PACKAGE,
    GET_CRYPTO_AMOUNT,
    GET_BROADCAST_MESSAGE,
    CRYPTO_PAYMENT,
    RATING,
    FEEDBACK,
    PROCESSING,
    GET_FEEDBACK_TEXT,
    GET_TARGETED_BROADCAST_MESSAGE,
    GET_LANGUAGE,
    GET_BANNER,
    GET_YOOKASSA_EMAIL, # Added
    YOOKASSA_PAYMENT,
    GET_BROADCAST_W_PRICES_MESSAGE,
    GET_BRAINROT
)

def get_conv_handler():
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & 
                (filters.Regex(r'youtube\.com/') | filters.Regex(r'youtu\.be/') | filters.Regex(r'twitch\.tv/')), 
                url_entrypoint
            ),
            CallbackQueryHandler(start_demo, pattern='^start_demo$'),
            CommandHandler("topup", topup_start),
            CallbackQueryHandler(topup_start, pattern='^topup_start$'),
            CommandHandler("broadcast", broadcast_start),
            CommandHandler("broadcast_to", broadcast_to_start),
            CommandHandler("broadcast_w_prices", broadcast_w_prices_start),
            CommandHandler("feedback", start_feedback)
        ],
        states={
            GET_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_url)],
            GET_SHORTS_NUMBER: [
                CallbackQueryHandler(get_shorts_number_auto, pattern='^auto$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_shorts_number_manual),
            ],
            GET_LAYOUT: [
                CallbackQueryHandler(get_layout, pattern='^(9_16|16_9|1_1)$'),
            ],
            GET_BRAINROT: [
                CallbackQueryHandler(get_brainrot, pattern='^(gta|minecraft|subway_surfers|slitherio|forza|bouncing_balls|ball_escape|no_brainrot)$'),
            ],
            GET_FACE_TRACKING: [
                CallbackQueryHandler(get_face_tracking, pattern='^(track_yes|track_no)$'),
            ],
            GET_BOTTOM_VIDEO: [
                CallbackQueryHandler(get_bottom_video, pattern='^(gta|minecraft|none)$'),
            ],
            GET_SUBTITLES_TYPE: [
                CallbackQueryHandler(get_subtitles_type, pattern='^(word-by-word|phrases|no_subtitles)$'),
            ],
            GET_SUBTITLE_STYLE: [
                CallbackQueryHandler(get_subtitle_style, pattern='^(white|yellow|purple|green)$'),
            ],
            GET_BANNER: [
                CallbackQueryHandler(get_banner_choice, pattern='^banner_(yes|no)$')
            ],
            CONFIRM_CONFIG: [
                CallbackQueryHandler(confirm_config, pattern='^confirm$'),
                CallbackQueryHandler(confirm_demo, pattern='^confirm_demo$'),
                CallbackQueryHandler(cancel_conversation, pattern='^cancel$'),
                CallbackQueryHandler(handle_check_subscription_reward, pattern='^check_subscription_reward$')
            ],
            PROCESSING: [
                CallbackQueryHandler(handle_rating, pattern='^rate_')
            ],
            FEEDBACK: [
                MessageHandler(filters.TEXT & (filters.Regex(r'youtube\.com/') | filters.Regex(r'youtu\.be/')), get_url),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_feedback),
                CallbackQueryHandler(skip_feedback, pattern='^skip_feedback$')
            ],
            GET_TOPUP_PACKAGE: [
                CallbackQueryHandler(select_topup_package, pattern=r'^topup_package_\d+_'),
            ],
            GET_TOPUP_METHOD: [
                CallbackQueryHandler(topup_stars, pattern='^topup_stars$'),
                CallbackQueryHandler(topup_crypto, pattern='^topup_crypto$'),
                CallbackQueryHandler(topup_yookassa, pattern='^topup_yookassa$'),
                CallbackQueryHandler(back_to_package_selection, pattern='^back_to_package_selection$'),
            ],
            GET_YOOKASSA_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_yookassa_email),
            ],
            CRYPTO_PAYMENT: [
                CallbackQueryHandler(check_crypto_payment, pattern='^check_crypto:'),
                CallbackQueryHandler(back_to_package_selection, pattern='^back_to_package_selection$'),
            ],
            YOOKASSA_PAYMENT: [
                CallbackQueryHandler(check_yookassa_payment, pattern='^check_yookassa:'),
                CallbackQueryHandler(back_to_package_selection, pattern='^back_to_package_selection$'),
            ],
            GET_BROADCAST_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_message)],
            GET_TARGETED_BROADCAST_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_to_message)],
            GET_BROADCAST_W_PRICES_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_w_prices_message)],
            GET_FEEDBACK_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_feedback)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True
    )
    return conv_handler