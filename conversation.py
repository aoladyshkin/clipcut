from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from commands import start, topup_start, broadcast_start, broadcast_message, cancel
from handlers import (
    get_url,
    get_shorts_number_auto,
    get_shorts_number_manual,
    get_layout,
    get_bottom_video,
    get_subtitles_type,
    get_subtitle_style,
    confirm_config,
    cancel_conversation,
    topup_stars,
    topup_crypto,
    cancel_topup,
    send_invoice_for_stars,
    get_crypto_amount,
    back_to_topup_method,
    check_crypto_payment,
    handle_rating,
    handle_feedback,
    skip_feedback
) 
from states import (
    GET_URL,
    GET_SUBTITLE_STYLE,
    GET_BOTTOM_VIDEO,
    GET_LAYOUT,
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
    PROCESSING
)

def get_conv_handler():
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("topup", topup_start),
            CallbackQueryHandler(topup_start, pattern='^topup_start'),
            CommandHandler("broadcast", broadcast_start)
        ],
        states={
            GET_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_url)],
            GET_SHORTS_NUMBER: [
                CallbackQueryHandler(get_shorts_number_auto, pattern='^auto'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_shorts_number_manual),
            ],
            GET_LAYOUT: [
                CallbackQueryHandler(get_layout, pattern='^(top_bottom|main_only)'),
            ],
            GET_BOTTOM_VIDEO: [
                CallbackQueryHandler(get_bottom_video, pattern='^(gta|minecraft|none)'),
            ],
            GET_SUBTITLES_TYPE: [
                CallbackQueryHandler(get_subtitles_type, pattern='^(word-by-word|phrases|no_subtitles)'),
            ],
            GET_SUBTITLE_STYLE: [
                CallbackQueryHandler(get_subtitle_style, pattern='^(white|yellow)'),
            ],
            CONFIRM_CONFIG: [
                CallbackQueryHandler(confirm_config, pattern='^confirm'),
                CallbackQueryHandler(cancel_conversation, pattern='^cancel'),
            ],
            PROCESSING: [
                CallbackQueryHandler(handle_rating, pattern='^rate_')
            ],
            FEEDBACK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_feedback),
                CallbackQueryHandler(skip_feedback, pattern='^skip_feedback')
            ],
            GET_TOPUP_METHOD: [
                CallbackQueryHandler(topup_stars, pattern='^topup_stars'),
                CallbackQueryHandler(topup_crypto, pattern='^topup_crypto'),
                CallbackQueryHandler(cancel_topup, pattern='^cancel_topup')
            ],
            GET_TOPUP_PACKAGE: [
                CallbackQueryHandler(send_invoice_for_stars, pattern='^topup_\d+_\d+'),
                CallbackQueryHandler(back_to_topup_method, pattern='^back_to_topup_method'),
                CallbackQueryHandler(cancel_topup, pattern='^cancel_topup')
            ],
            GET_CRYPTO_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_crypto_amount),
                CallbackQueryHandler(back_to_topup_method, pattern='^back_to_topup_method'),
                CallbackQueryHandler(cancel_topup, pattern='^cancel_topup')
            ],
            CRYPTO_PAYMENT: [
                CallbackQueryHandler(check_crypto_payment, pattern='^check_crypto:'),
                CallbackQueryHandler(back_to_topup_method, pattern='^back_to_topup_method'),
                CallbackQueryHandler(cancel_topup, pattern='^cancel_topup')
            ],
            GET_BROADCAST_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_message)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True
    )
    return conv_handler
