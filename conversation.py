from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from commands import start, topup_start
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
    back_to_topup_method
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
    GET_CRYPTO_AMOUNT
)



def get_conv_handler():
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start), CommandHandler("topup", topup_start), CallbackQueryHandler(topup_start, pattern='^topup_start')],
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
            ]
        },
        fallbacks=[CommandHandler("start", start)],
        conversation_timeout=600, # 10 минут на диалог
        allow_reentry=True
    )
    return conv_handler
